"""Bazel adapter — measurement + diagnose surface for the build skills.

Measurement (``ios-build-measure``): wraps ``bazelisk build --profile=<path>``,
captures wall-clock with ``time.monotonic`` around the subprocess, and returns
a :class:`TimedBuild` shape-compatible with the xcode adapter
(``result_bundle_path`` is ``None`` for Bazel — the chrome-trace profile lives
alongside the stdout log at ``<output_dir>/build-<kind>-<repeat>-profile.json``
and is consumed by ``scripts/critical_path.py``).

Diagnose (``ios-build-diagnose``) — added in v1.2:

* :func:`script_phases` parses ``bazel query 'kind(genrule, //...)'
  --output=xml`` and exposes each genrule as a
  :class:`ScriptPhase` so the upstream analyzer can flag missing-output
  declarations, always-out-of-date markers, and similar smells.
* :func:`package_graph` reads ``Package.resolved`` (workspace-level)
  and any per-package ``Package.resolved`` to extract :class:`Pin`
  entries, then walks the project tree for local ``Package.swift``
  modules. Mirrors the xcode adapter's behaviour.
* :func:`show_build_settings` runs ``bazel info`` plus a curated
  ``bazel cquery --output=jsonproto`` against the requested target and
  flattens the per-rule attributes that matter for the v1.0 rules
  (compilation_mode, copts, swiftcopts, features, etc.). Not all xcode
  build settings have Bazel analogues; missing keys are simply absent.

Argument mapping (the adapter signature is shared with xcode_adapter so
benchmark.py can call into either without branching):

* ``scheme`` -> Bazel target label, e.g. ``"//App:SmokeLib"``. The CLI exposes
  this as ``--scheme`` to keep parity with the xcode flow.
* ``configuration`` -> Bazel ``--config=<name>`` value, e.g. ``"ios_sim"``. The
  default is ``"ios_sim"`` because iOS-simulator is the v1 platform fence.
* ``destination`` -> ignored (xcodebuild-specific). A note is logged so users
  don't think the value flowed through.
* ``touch_file`` -> for ``kind=="incremental"``, ``touch_file.touch()`` exactly
  as xcode_adapter does; Bazel's file-content-hash invalidation will rebuild
  the touched compilation unit and everything downstream.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from . import LocalModule, PackageGraph, Pin, ScriptPhase, TimedBuild, require_ios


def detect(project_path: pathlib.Path) -> bool:
    """Return True when ``project_path`` carries Bazel root + at least one BUILD."""
    has_root = (
        (project_path / "MODULE.bazel").is_file()
        or (project_path / "WORKSPACE").is_file()
        or (project_path / "WORKSPACE.bazel").is_file()
    )
    if not has_root:
        return False
    for build_name in ("BUILD", "BUILD.bazel"):
        for _ in project_path.rglob(build_name):
            return True
    return False


def _find_bazel_binary() -> str:
    """Return the path to a usable Bazel launcher.

    Preference order: ``bazelisk`` (auto-resolves the version pinned in
    ``.bazelversion``), then ``bazel``. Both are looked up on ``PATH``; we
    do not invoke a vendored binary because the launcher version drift
    between v1.x of this skill and the target project would mask real
    measurement issues.
    """

    for candidate in ("bazelisk", "bazel"):
        resolved = shutil.which(candidate)
        if resolved is not None:
            return resolved
    raise FileNotFoundError(
        "no `bazelisk` or `bazel` launcher found on PATH; install Bazelisk "
        "via `brew install bazelisk` (or equivalent) before running "
        "ios-build-measure against a Bazel project."
    )


def _run_clean(project_path: pathlib.Path, bazel_binary: str) -> None:
    """Best-effort ``bazel clean --expunge`` for a true clean build.

    ``--expunge`` wipes the entire output base, equivalent to
    ``xcodebuild clean`` + DerivedData removal. Failures are logged but
    not fatal — the subsequent build will still produce a wall-clock
    number, just one that may include some cached state.
    """

    subprocess.run(
        [bazel_binary, "clean", "--expunge"],
        cwd=project_path,
        check=False,
        capture_output=True,
    )


def _build_bazel_args(
    bazel_binary: str,
    scheme: str,
    configuration: str,
    profile_path: pathlib.Path,
    extra_args: list[str] | None,
) -> list[str]:
    """Construct the ``bazelisk build`` argv with profile capture.

    The target label is required; we surface it as ``scheme`` to keep
    benchmark.py's CLI shared across adapters.
    """

    if not scheme:
        raise ValueError(
            "Bazel measurement requires --scheme to be a Bazel target label "
            "(e.g. //App:SmokeLib); empty schemes are an xcode-only convenience."
        )

    args: list[str] = [bazel_binary, "build"]
    if configuration:
        args.append(f"--config={configuration}")
    args.append(f"--profile={profile_path}")
    args.append(scheme)
    if extra_args:
        args.extend(extra_args)
    return args


def _time_one_build(
    project_path: pathlib.Path,
    argv: list[str],
    log_path: pathlib.Path,
    kind: str,
) -> TimedBuild:
    """Run a single Bazel build and return the timing record."""

    log_path.parent.mkdir(parents=True, exist_ok=True)

    started_wall = datetime.now(timezone.utc).isoformat(timespec="seconds")
    started_mono = time.monotonic()
    with log_path.open("wb") as log_fh:
        completed = subprocess.run(
            argv,
            cwd=project_path,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            check=False,
        )
    duration = time.monotonic() - started_mono
    finished_wall = datetime.now(timezone.utc).isoformat(timespec="seconds")

    return TimedBuild(
        kind=kind,
        duration_seconds=duration,
        exit_code=completed.returncode,
        stdout_log_path=str(log_path),
        result_bundle_path=None,
        started_at=started_wall,
        finished_at=finished_wall,
    )


def measure(
    project_path: pathlib.Path,
    scheme: str | None,
    configuration: str,
    destination: str,
    platform: str = "ios",
    touch_file: pathlib.Path | None = None,
    kind: str = "clean",
    output_dir: pathlib.Path | None = None,
    repeat_index: int = 0,
    extra_xcodebuild_args: list[str] | None = None,
) -> TimedBuild:
    """Run one Bazel build of the requested ``kind`` and return the timing.

    The signature mirrors :func:`xcode_adapter.measure` so benchmark.py can
    call either adapter without branching. ``destination`` is xcodebuild-
    specific and is ignored here (a note is printed once per run).
    ``extra_xcodebuild_args`` is forwarded to ``bazelisk build`` as extra args
    so users can pass e.g. ``--remote_cache=…`` through the same CLI knob.
    """

    require_ios(platform)
    if output_dir is None:
        raise ValueError("output_dir is required (used for log + profile paths)")
    if not scheme:
        raise ValueError(
            "Bazel measurement requires --scheme to be a Bazel target label "
            "(e.g. //App:SmokeLib)."
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    bazel_binary = _find_bazel_binary()

    log_path = output_dir / f"build-{kind}-{repeat_index}.log"
    profile_path = output_dir / f"build-{kind}-{repeat_index}-profile.json"

    if destination:
        print(
            f"[bazel-adapter] ignoring --destination={destination!r} "
            f"(xcodebuild-specific; Bazel selects platform via --config).",
            file=sys.stderr,
        )

    if kind == "clean":
        _run_clean(project_path, bazel_binary)
    elif kind == "incremental":
        if touch_file is None:
            raise ValueError(
                "incremental measurement requires --touch-file PATH; "
                "Bazel rebuilds the touched file's compilation unit + "
                "downstream targets while keeping the rest of the action "
                "cache warm."
            )
        if not touch_file.exists():
            raise FileNotFoundError(f"touch_file does not exist: {touch_file}")
        touch_file.touch()
    else:
        raise ValueError(
            f"unsupported kind={kind!r}; expected clean or incremental"
        )

    argv = _build_bazel_args(
        bazel_binary,
        scheme,
        configuration,
        profile_path,
        extra_xcodebuild_args,
    )

    return _time_one_build(project_path, argv, log_path, kind)


# --- diagnose surface (v1.2) -----------------------------------------------


def _run_bazel_query(
    project_path: pathlib.Path,
    query: str,
    output_format: str,
    bazel_binary: str | None = None,
) -> str | None:
    """Run ``bazel query`` and return stdout, or ``None`` on failure.

    Failure modes (missing binary, malformed BUILD files, non-zero exit)
    are logged to stderr but never raised — diagnose downstream treats
    an absent result as "no findings of this kind" and moves on.
    """

    if bazel_binary is None:
        try:
            bazel_binary = _find_bazel_binary()
        except FileNotFoundError as exc:
            print(f"[bazel-adapter] {exc}", file=sys.stderr)
            return None

    argv = [
        bazel_binary,
        "query",
        query,
        f"--output={output_format}",
    ]
    try:
        completed = subprocess.run(
            argv,
            cwd=project_path,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        print(f"[bazel-adapter] bazel query failed: {exc}", file=sys.stderr)
        return None
    if completed.returncode != 0:
        print(
            f"[bazel-adapter] bazel query exited rc={completed.returncode}: "
            f"{(completed.stderr or '').strip()[:200]}",
            file=sys.stderr,
        )
        return None
    return completed.stdout


_GENRULE_ALWAYS_OUT_OF_DATE_TAGS = frozenset({"no-cache", "no-remote-cache", "local"})


def _parse_query_xml_genrules(xml_text: str) -> list[ScriptPhase]:
    """Parse ``bazel query --output=xml`` output for genrule entries."""

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        print(f"[bazel-adapter] query xml parse error: {exc}", file=sys.stderr)
        return []

    phases: list[ScriptPhase] = []
    for rule in root.findall("rule"):
        if rule.attrib.get("class") != "genrule":
            continue
        label = rule.attrib.get("name", "")
        location = rule.attrib.get("location", "")
        target, name = _split_genrule_label(label)

        cmd = _first_xml_string(rule, "cmd")
        srcs = tuple(_xml_list_values(rule, "srcs"))
        outs = tuple(_xml_list_values(rule, "outs"))
        tags = set(_xml_list_values(rule, "tags"))

        always_out_of_date = bool(
            tags & _GENRULE_ALWAYS_OUT_OF_DATE_TAGS
        ) or not outs

        phases.append(
            ScriptPhase(
                target=target,
                name=name,
                script=cmd or "",
                input_paths=srcs,
                output_paths=outs,
                always_out_of_date=always_out_of_date,
                pbxproj_path=location,
            )
        )
    return phases


def _split_genrule_label(label: str) -> tuple[str, str]:
    """Split ``//pkg:name`` into ``("//pkg", "name")``; fall back to the
    full label on either side if the format is unexpected."""

    if ":" in label:
        target, name = label.rsplit(":", 1)
        return target, name
    return label, label


def _first_xml_string(rule: ET.Element, attr_name: str) -> str | None:
    """Return the first ``<string name='attr_name'>`` value, else ``None``."""

    for node in rule.findall("string"):
        if node.attrib.get("name") == attr_name:
            return node.attrib.get("value")
    return None


def _xml_list_values(rule: ET.Element, attr_name: str) -> list[str]:
    """Return the list values for a ``<list name='attr_name'>`` element."""

    for node in rule.findall("list"):
        if node.attrib.get("name") != attr_name:
            continue
        values: list[str] = []
        for child in node:
            value = child.attrib.get("value")
            if value is not None:
                values.append(value)
        return values
    return []


def script_phases(
    project_path: pathlib.Path,
    platform: str = "ios",
) -> list[ScriptPhase]:
    """Return ``genrule`` entries as Xcode-style :class:`ScriptPhase` records.

    Uses ``bazel query 'kind(genrule, //...)' --output=xml``; on query
    failure (missing bazelisk binary, malformed BUILD files) returns an
    empty list so ``ios-build-diagnose`` produces zero findings rather
    than crashing. The ``always_out_of_date`` flag fires when the
    genrule tags include ``no-cache``/``no-remote-cache``/``local`` OR
    when the genrule declares no ``outs`` (cacheless by definition).
    """

    require_ios(platform)
    xml_text = _run_bazel_query(
        project_path,
        "kind(genrule, //...)",
        "xml",
    )
    if xml_text is None:
        return []
    return _parse_query_xml_genrules(xml_text)


# --- package_graph ----------------------------------------------------------


def _read_package_resolved(path: pathlib.Path) -> list[Pin]:
    """Parse one Package.resolved (SPM v2 or v3 schema) into Pin records."""

    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []

    # SPM v2 (Xcode 13+): {"object": {"pins": [...]}}; v3: {"pins": [...]}.
    pins_raw = data.get("pins")
    if pins_raw is None and isinstance(data.get("object"), dict):
        pins_raw = data["object"].get("pins")
    if not isinstance(pins_raw, list):
        return []

    pins: list[Pin] = []
    for entry in pins_raw:
        if not isinstance(entry, dict):
            continue
        identity = entry.get("identity") or entry.get("package")
        if not isinstance(identity, str):
            continue
        location = entry.get("location") or (entry.get("repositoryURL") or "")
        state = entry.get("state") or {}
        if not isinstance(state, dict):
            state = {}
        pins.append(
            Pin(
                name=identity,
                version=state.get("version"),
                revision=state.get("revision"),
                branch=state.get("branch"),
                location=location,
                source_resolved_path=str(path),
            )
        )
    return pins


def _walk_local_swift_packages(project_path: pathlib.Path) -> list[LocalModule]:
    """Return the list of in-tree ``Package.swift`` modules with swift-file counts.

    Mirrors the xcode adapter's behaviour: each ``Package.swift`` is a
    local SPM package; we count ``.swift`` files under its ``Sources/``
    directory (the SwiftPM convention). Modules without a ``Sources/``
    directory are skipped — they may still appear in the
    ``swift_deps.from_package`` Bazel extension but they don't ship
    Swift sources for the analyzer to count.
    """

    modules: list[LocalModule] = []
    for manifest in project_path.rglob("Package.swift"):
        sources_dir = manifest.parent / "Sources"
        if not sources_dir.is_dir():
            continue
        for module_dir in sorted(p for p in sources_dir.iterdir() if p.is_dir()):
            count = sum(1 for _ in module_dir.rglob("*.swift"))
            modules.append(
                LocalModule(
                    name=module_dir.name,
                    path=str(module_dir),
                    source_count=count,
                )
            )
    return modules


def package_graph(
    project_path: pathlib.Path,
    platform: str = "ios",
) -> PackageGraph:
    """Walk ``Package.resolved`` files + local ``Package.swift`` modules.

    The pin set is de-duplicated by ``(name, source_resolved_path)`` so
    the same package pinned in two adjacent workspaces shows up twice
    only when it really does (preserves the upstream xcode_adapter
    behaviour). Returns an empty :class:`PackageGraph` when neither a
    resolved file nor a manifest exists; the upstream analyzer treats
    that as "no SPM presence" and moves on.
    """

    require_ios(platform)

    seen: set[tuple[str, str]] = set()
    pins: list[Pin] = []
    for resolved in project_path.rglob("Package.resolved"):
        for pin in _read_package_resolved(resolved):
            key = (pin.name, pin.source_resolved_path)
            if key in seen:
                continue
            seen.add(key)
            pins.append(pin)

    local_modules = _walk_local_swift_packages(project_path)

    return PackageGraph(pins=tuple(pins), local_modules=tuple(local_modules))


# --- show_build_settings ----------------------------------------------------


_INFO_KEYS_TO_EXPOSE = (
    "release",
    "java-runtime",
    "java-home",
    "command_log",
    "execution_root",
    "output_path",
    "workspace",
)


def _run_bazel_info(
    project_path: pathlib.Path,
    bazel_binary: str | None = None,
) -> dict[str, str]:
    """Capture key=value lines from ``bazel info`` into a dict.

    Used by :func:`show_build_settings` to surface a small set of
    workspace facts the upstream rules can lean on (build root, output
    path, Bazel release). ``bazel info`` is intentionally fast — it
    does not analyse targets — so this stays cheap even on large repos.
    """

    if bazel_binary is None:
        try:
            bazel_binary = _find_bazel_binary()
        except FileNotFoundError:
            return {}

    try:
        completed = subprocess.run(
            [bazel_binary, "info"],
            cwd=project_path,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return {}
    if completed.returncode != 0:
        return {}

    out: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key and value:
            out[key] = value
    return out


_CQUERY_ATTR_KEYS = (
    "compilation_mode",
    "copts",
    "swiftcopts",
    "linkopts",
    "features",
    "module_name",
    "alwayslink",
    "enable_modules",
    "generates_header",
    "library_evolution",
)


def _run_bazel_cquery(
    project_path: pathlib.Path,
    target_label: str,
    configuration: str,
    bazel_binary: str | None = None,
) -> dict[str, str]:
    """Run ``bazel cquery <target> --output=jsonproto`` and flatten attrs.

    Returns a dict keyed by attribute name (e.g. ``"copts"``,
    ``"swiftcopts"``) with values stringified for the downstream
    analyzer. Bazel's cquery is target-resolved, so multi-arch fan-out
    flattens to whichever configuration the command was run in;
    ``--config=<configuration>`` selects the same toolchain
    ``ios-build-measure`` is benchmarking.
    """

    if bazel_binary is None:
        try:
            bazel_binary = _find_bazel_binary()
        except FileNotFoundError:
            return {}

    argv = [bazel_binary, "cquery", target_label, "--output=jsonproto"]
    if configuration:
        argv.append(f"--config={configuration}")
    try:
        completed = subprocess.run(
            argv,
            cwd=project_path,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return {}
    if completed.returncode != 0:
        return {}

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}

    out: dict[str, str] = {}
    for result in payload.get("results", []):
        target = result.get("target", {})
        rule = target.get("rule", {})
        for attribute in rule.get("attribute", []):
            name = attribute.get("name")
            if name not in _CQUERY_ATTR_KEYS:
                continue
            value = (
                attribute.get("stringValue")
                or attribute.get("booleanValue")
                or attribute.get("stringListValue")
                or attribute.get("intValue")
            )
            if value is None:
                continue
            if isinstance(value, list):
                value = " ".join(str(v) for v in value)
            out[name] = str(value)
    return out


def show_build_settings(
    project_path: pathlib.Path,
    scheme: str | None,
    configuration: str,
    platform: str = "ios",
) -> dict[str, str]:
    """Return Bazel-side analogue of ``xcodebuild -showBuildSettings``.

    Combines ``bazel info`` (workspace facts) with ``bazel cquery
    <scheme> --output=jsonproto`` (per-target attributes that matter
    for the upstream v1.0 rules). Returns ``{}`` on any failure; the
    downstream analyzer treats an empty dict as "build-setting findings
    short-circuit", which is the correct behaviour when the data is
    unreachable.

    Not every xcodebuild setting has a Bazel analogue (and vice-versa).
    The keys that DO map are exposed; the rest are simply absent. The
    upstream build_setting analyzer treats missing keys as "no
    evidence", not "default".
    """

    require_ios(platform)

    settings: dict[str, str] = {}
    info = _run_bazel_info(project_path)
    for key in _INFO_KEYS_TO_EXPOSE:
        if key in info:
            settings[f"bazel.info.{key}"] = info[key]

    if scheme:
        attrs = _run_bazel_cquery(project_path, scheme, configuration)
        for key, value in attrs.items():
            settings[f"bazel.target.{key}"] = value

    return settings


def adapter_label() -> str:
    return "bazel"
