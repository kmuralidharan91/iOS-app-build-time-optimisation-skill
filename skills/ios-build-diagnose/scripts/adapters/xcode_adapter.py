"""Xcode adapter — measurement (Phase A) + diagnose surface (Phase A).

Measurement strategy (Phase A):

1. Build the xcodebuild argv with -showBuildTimingSummary plus a
   per-run -resultBundlePath under the output directory. Stdout is
   tee'd to a log file so the timing-summary fallback parser can
   read it without re-running the build.
2. Time the subprocess wall-clock with a monotonic clock; do not
   trust xcodebuild's own self-reported number — that one excludes
   spawn / cleanup overhead.
3. Capture .xcresult bundle path so scripts/critical_path.py can
   parse the per-target build summary via xcrun xcresulttool.

Diagnose strategy (Phase A):

- show_build_settings: ask xcodebuild to resolve the *effective*
  value of every build setting per scheme + configuration via the
  -showBuildSettings -json flag. Falls back to {} when xcodebuild
  is missing, the network is down (private SPM mirrors), or output
  is non-JSON. Analyzers consume the resolved dict and use the
  PR-#1 state table: explicit value -> audit literally; unset +
  resolved-by-default -> pass; unset + xcodebuild silent -> fall
  back to (unset).
- script_phases: parse every *.xcodeproj/project.pbxproj plist
  (after plutil -convert xml1) and walk PBXNativeTarget /
  PBXAggregateTarget buildPhases arrays to recover every
  PBXShellScriptBuildPhase with its target attribution, name,
  shell body, input/output paths, alwaysOutOfDate flag, and shell
  path.
- package_graph: walk **/Package.resolved JSON files (workspace +
  per-package nested resolved files) plus any *.xcodeproj for
  XCRemoteSwiftPackageReference entries; enumerate local SPM
  modules under the project tree by their Package.swift manifests
  and count *.swift sources to identify oversized ones.

Apply-fix (Phase A) remains stubbed.
"""

from __future__ import annotations

import json
import os
import pathlib
import plistlib
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import TimedBuild, require_ios


def detect(project_path: pathlib.Path) -> bool:
    """Return True when ``project_path`` looks like a stock Xcode project."""

    has_xcodeproj = any(project_path.glob("*.xcodeproj"))
    has_xcworkspace = any(project_path.glob("*.xcworkspace"))
    return has_xcodeproj or has_xcworkspace


def find_workspace_or_project(project_path: pathlib.Path) -> tuple[str, pathlib.Path]:
    """Pick a workspace if present, otherwise the first xcodeproj.

    Returns a (flag, path) tuple where ``flag`` is ``"-workspace"`` or
    ``"-project"`` for direct passing to ``xcodebuild``.
    """

    workspaces = sorted(project_path.glob("*.xcworkspace"))
    if workspaces:
        return ("-workspace", workspaces[0])
    projects = sorted(project_path.glob("*.xcodeproj"))
    if not projects:
        raise FileNotFoundError(
            f"no *.xcodeproj or *.xcworkspace at {project_path}"
        )
    return ("-project", projects[0])


def build_xcodebuild_args(
    project_path: pathlib.Path,
    scheme: str | None,
    configuration: str,
    destination: str,
    result_bundle_path: pathlib.Path | None,
    extra_xcodebuild_args: list[str] | None = None,
) -> list[str]:
    """Compose the xcodebuild argv for a single build invocation."""

    flag, container = find_workspace_or_project(project_path)
    args: list[str] = ["xcodebuild", flag, str(container)]
    if scheme:
        args.extend(["-scheme", scheme])
    args.extend([
        "-configuration", configuration,
        "-destination", destination,
        "-showBuildTimingSummary",
    ])
    if result_bundle_path is not None:
        # Xcode refuses to overwrite an existing result bundle; the
        # caller is responsible for choosing a fresh path per run.
        args.extend(["-resultBundlePath", str(result_bundle_path)])
    if extra_xcodebuild_args:
        args.extend(extra_xcodebuild_args)
    args.append("build")
    return args


def run_clean(
    project_path: pathlib.Path,
    scheme: str | None,
    configuration: str,
    destination: str,
) -> None:
    """Invoke ``xcodebuild clean`` (best-effort; failures are logged but not fatal)."""

    flag, container = find_workspace_or_project(project_path)
    argv: list[str] = ["xcodebuild", flag, str(container)]
    if scheme:
        argv.extend(["-scheme", scheme])
    argv.extend([
        "-configuration", configuration,
        "-destination", destination,
        "clean",
    ])
    subprocess.run(argv, cwd=project_path, check=False, capture_output=True)


def wipe_derived_data() -> None:
    """Remove ~/Library/Developer/Xcode/DerivedData so a clean build is truly clean.

    A clean build that re-uses DerivedData reads cached module
    interfaces; that masks SwiftCompile costs we want to measure.
    Wipe only when ``IOS_BUILD_MEASURE_WIPE_DERIVED_DATA`` is set,
    otherwise rely on ``xcodebuild clean`` for the same target.
    """

    if os.environ.get("IOS_BUILD_MEASURE_WIPE_DERIVED_DATA") != "1":
        return
    derived = pathlib.Path.home() / "Library/Developer/Xcode/DerivedData"
    if derived.exists():
        shutil.rmtree(derived, ignore_errors=True)


def time_one_build(
    project_path: pathlib.Path,
    scheme: str | None,
    configuration: str,
    destination: str,
    kind: str,
    log_path: pathlib.Path,
    result_bundle_path: pathlib.Path | None,
    extra_xcodebuild_args: list[str] | None = None,
) -> TimedBuild:
    """Run a single xcodebuild build and return the timing record.

    The wall-clock is measured with ``time.monotonic`` around the
    subprocess; stdout is captured to ``log_path`` so the timing-summary
    fallback parser can re-read it later without re-running the build.
    """

    argv = build_xcodebuild_args(
        project_path,
        scheme,
        configuration,
        destination,
        result_bundle_path,
        extra_xcodebuild_args,
    )

    log_path.parent.mkdir(parents=True, exist_ok=True)
    if result_bundle_path is not None and result_bundle_path.exists():
        # xcodebuild refuses to overwrite; remove a stale bundle.
        shutil.rmtree(result_bundle_path)

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
        result_bundle_path=str(result_bundle_path) if result_bundle_path else None,
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
    """Run one build of the requested ``kind`` ("clean" or "incremental").

    Caller is responsible for invoking ``measure`` once per repeat and
    aggregating the resulting :class:`TimedBuild` records into the final
    benchmark artifact (see scripts/benchmark.py).
    """

    require_ios(platform)
    if output_dir is None:
        raise ValueError("output_dir is required (used for log + xcresult paths)")
    output_dir.mkdir(parents=True, exist_ok=True)

    log_path = output_dir / f"build-{kind}-{repeat_index}.log"
    bundle_path = output_dir / f"build-{kind}-{repeat_index}.xcresult"

    if kind == "clean":
        wipe_derived_data()
        run_clean(project_path, scheme, configuration, destination)
    elif kind == "incremental":
        if touch_file is None:
            raise ValueError(
                "incremental measurement requires --touch-file PATH; "
                "see ios-build-measure SKILL.md 'Workflow' for details."
            )
        if not touch_file.exists():
            raise FileNotFoundError(f"touch_file does not exist: {touch_file}")
        # Updating mtime forces xcodebuild to recompile this file and
        # everything downstream while keeping every other file's
        # incremental state intact.
        touch_file.touch()
    else:
        raise ValueError(f"unsupported kind={kind!r}; expected clean or incremental")

    return time_one_build(
        project_path,
        scheme,
        configuration,
        destination,
        kind,
        log_path,
        bundle_path,
        extra_xcodebuild_args,
    )


# --- diagnose surface (Phase A) -------------------------------------------


@dataclass(frozen=True)
class ScriptPhase:
    """One PBXShellScriptBuildPhase entry recovered from a pbxproj plist.

    ``script`` is the raw shell body text. ``input_paths`` /
    ``output_paths`` are verbatim from the pbxproj arrays; an empty
    list means the phase did not declare them. ``always_out_of_date``
    is True when the pbxproj field is the integer 1 (Xcode's
    "Based on dependency analysis" checkbox is OFF) and False when
    the field is 0 or absent.
    """

    target: str
    name: str
    script: str
    input_paths: tuple[str, ...]
    output_paths: tuple[str, ...]
    always_out_of_date: bool
    shell_path: str


@dataclass(frozen=True)
class PackagePin:
    """One SPM dependency pin from a Package.resolved file."""

    name: str
    version: str | None
    revision: str | None
    branch: str | None
    location: str
    source_resolved_path: str


@dataclass(frozen=True)
class LocalModule:
    """One on-disk Swift Package found under the project tree."""

    name: str
    path: str
    source_count: int


@dataclass(frozen=True)
class PackageGraph:
    pins: tuple[PackagePin, ...]
    local_modules: tuple[LocalModule, ...]
    remote_xcodeproj_refs: tuple[str, ...]


def show_build_settings(
    project_path: pathlib.Path,
    scheme: str | None,
    configuration: str,
    platform: str = "ios",
    timeout_seconds: int = 60,
) -> dict[str, str]:
    """Return the resolved build settings dict for the requested config.

    Implements the PR-#1 effective-settings strategy: ask xcodebuild for
    the resolved value of every setting, returning a flat
    ``{setting_key: value}`` dict. The caller's analyzer applies the
    state table (explicit pbxproj value -> audit literally; unset +
    matching default -> pass; unset + mismatch -> fail; xcodebuild
    silent -> fall back to ``(unset)``).

    Returns ``{}`` and emits a stderr note when xcodebuild is missing,
    the subprocess exits non-zero, the timeout fires, or the output is
    not valid JSON. Stdlib only.
    """

    require_ios(platform)
    if shutil.which("xcodebuild") is None:
        print(
            "[xcode_adapter.show_build_settings] xcodebuild not on PATH; "
            "returning empty resolved-settings dict",
            file=sys.stderr,
        )
        return {}

    try:
        flag, container = find_workspace_or_project(project_path)
    except FileNotFoundError as exc:
        print(
            f"[xcode_adapter.show_build_settings] {exc}; "
            "returning empty resolved-settings dict",
            file=sys.stderr,
        )
        return {}

    argv: list[str] = ["xcodebuild", flag, str(container)]
    if scheme:
        argv.extend(["-scheme", scheme])
    argv.extend([
        "-configuration", configuration,
        "-showBuildSettings",
        "-json",
    ])

    try:
        completed = subprocess.run(
            argv,
            cwd=project_path,
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[xcode_adapter.show_build_settings] xcodebuild timed out after "
            f"{timeout_seconds}s; returning empty resolved-settings dict",
            file=sys.stderr,
        )
        return {}

    if completed.returncode != 0:
        snippet = completed.stderr.decode("utf-8", errors="replace")[:400]
        print(
            f"[xcode_adapter.show_build_settings] xcodebuild exit "
            f"{completed.returncode}: {snippet}",
            file=sys.stderr,
        )
        return {}

    raw = completed.stdout.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(
            f"[xcode_adapter.show_build_settings] non-JSON xcodebuild output: "
            f"{exc}; returning empty resolved-settings dict",
            file=sys.stderr,
        )
        return {}

    # xcodebuild -showBuildSettings -json returns a list of
    # {target, action, buildSettings} objects, one per resolved
    # target. Merge them with target settings overriding shared
    # ones; the analyzer cares about the final resolved value.
    merged: dict[str, str] = {}
    if isinstance(payload, list):
        for entry in payload:
            settings = entry.get("buildSettings", {}) if isinstance(entry, dict) else {}
            for key, value in settings.items():
                merged[str(key)] = str(value)
    return merged


def _convert_pbxproj_to_xml(pbxproj_path: pathlib.Path) -> bytes:
    """Run ``plutil -convert xml1 -o - <path>`` and return XML plist bytes.

    pbxproj files are stored as ASCII plists by default; plistlib only
    reads XML / binary. plutil ships with macOS and converts losslessly.
    """

    completed = subprocess.run(
        ["plutil", "-convert", "xml1", "-o", "-", str(pbxproj_path)],
        check=True,
        capture_output=True,
    )
    return completed.stdout


def _bool_from_pbxproj(value: Any) -> bool:
    """Coerce a pbxproj boolean-ish value to a Python bool.

    pbxproj uses the string ``"1"`` / ``"0"`` for booleans inside the
    ASCII format; after plutil conversion these arrive as either
    integer 1 / 0 or string ``"1"`` / ``"0"``.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip() == "1"
    return False


def script_phases(
    project_path: pathlib.Path,
    platform: str = "ios",
) -> list[ScriptPhase]:
    """Return every PBXShellScriptBuildPhase across all *.xcodeproj.

    Walks every ``*.xcodeproj/project.pbxproj`` reachable from the
    project root (including ones nested under local SPM packages),
    converts each to XML via ``plutil``, parses with ``plistlib``,
    and recovers per-phase fields plus the owning target's name.

    Targets that reference a phase but live in a different
    project.pbxproj are not cross-resolved — every phase is returned
    against the target as named in its own pbxproj's
    PBXNativeTarget / PBXAggregateTarget records.
    """

    require_ios(platform)
    phases: list[ScriptPhase] = []

    pbxproj_paths = sorted(project_path.rglob("project.pbxproj"))
    for pbx_path in pbxproj_paths:
        if "/Pods/" in str(pbx_path):
            continue
        try:
            xml_bytes = _convert_pbxproj_to_xml(pbx_path)
        except subprocess.CalledProcessError as exc:
            print(
                f"[xcode_adapter.script_phases] plutil failed on {pbx_path}: "
                f"exit {exc.returncode}; skipping",
                file=sys.stderr,
            )
            continue
        try:
            plist = plistlib.loads(xml_bytes)
        except plistlib.InvalidFileException as exc:
            print(
                f"[xcode_adapter.script_phases] plistlib could not parse "
                f"{pbx_path}: {exc}; skipping",
                file=sys.stderr,
            )
            continue

        objects = plist.get("objects", {})
        if not isinstance(objects, dict):
            continue

        # Build a name lookup for every target's buildPhases pointer
        # before walking phases, so each phase lands on the right
        # target without a second pass.
        phase_owner: dict[str, str] = {}
        for obj_id, obj in objects.items():
            if not isinstance(obj, dict):
                continue
            isa = obj.get("isa")
            if isa not in {"PBXNativeTarget", "PBXAggregateTarget", "PBXLegacyTarget"}:
                continue
            target_name = str(obj.get("name", "<unnamed>"))
            for build_phase_id in obj.get("buildPhases", []) or []:
                phase_owner[str(build_phase_id)] = target_name

        for obj_id, obj in objects.items():
            if not isinstance(obj, dict):
                continue
            if obj.get("isa") != "PBXShellScriptBuildPhase":
                continue
            target = phase_owner.get(str(obj_id), "<orphan>")
            raw_name = obj.get("name")
            name = str(raw_name) if isinstance(raw_name, str) else "Run Script"
            script = str(obj.get("shellScript", ""))
            input_paths = tuple(str(p) for p in (obj.get("inputPaths") or []))
            output_paths = tuple(str(p) for p in (obj.get("outputPaths") or []))
            always_oot = _bool_from_pbxproj(obj.get("alwaysOutOfDate", 0))
            shell_path = str(obj.get("shellPath", "/bin/sh"))
            phases.append(
                ScriptPhase(
                    target=target,
                    name=name,
                    script=script,
                    input_paths=input_paths,
                    output_paths=output_paths,
                    always_out_of_date=always_oot,
                    shell_path=shell_path,
                )
            )
    return phases


_PACKAGE_RESOLVED_GLOB = "Package.resolved"


def _walk_package_resolved_files(project_path: pathlib.Path) -> list[pathlib.Path]:
    """Return every Package.resolved beneath project_path, ignoring DerivedData."""

    found: list[pathlib.Path] = []
    for candidate in project_path.rglob(_PACKAGE_RESOLVED_GLOB):
        sp = str(candidate)
        if "/DerivedData/" in sp:
            continue
        if "/.build/" in sp:
            continue
        found.append(candidate)
    return sorted(found)


def _parse_package_resolved(path: pathlib.Path) -> list[PackagePin]:
    """Parse one Package.resolved (v1 or v2 schema) into PackagePin records."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"[xcode_adapter.package_graph] could not read {path}: {exc}; "
            "skipping",
            file=sys.stderr,
        )
        return []

    pins_list: list[Any] = []
    if isinstance(payload, dict):
        # v2: top-level "pins" key. v1 (used by SPM CLI): payload["object"]["pins"].
        if "pins" in payload and isinstance(payload["pins"], list):
            pins_list = payload["pins"]
        elif (
            "object" in payload
            and isinstance(payload["object"], dict)
            and isinstance(payload["object"].get("pins"), list)
        ):
            pins_list = payload["object"]["pins"]

    out: list[PackagePin] = []
    for entry in pins_list:
        if not isinstance(entry, dict):
            continue
        # v2 keys: identity, kind, location, state{version,revision,branch}
        # v1 keys: package, repositoryURL, state{version,revision,branch}
        name = str(entry.get("identity") or entry.get("package") or "")
        location = str(entry.get("location") or entry.get("repositoryURL") or "")
        state = entry.get("state") or {}
        if not isinstance(state, dict):
            state = {}
        version = state.get("version")
        revision = state.get("revision")
        branch = state.get("branch")
        out.append(
            PackagePin(
                name=name,
                version=str(version) if version is not None else None,
                revision=str(revision) if revision is not None else None,
                branch=str(branch) if branch is not None else None,
                location=location,
                source_resolved_path=str(path),
            )
        )
    return out


def _enumerate_local_modules(project_path: pathlib.Path) -> list[LocalModule]:
    """Find every Package.swift under project_path and return source-count records."""

    out: list[LocalModule] = []
    for manifest in sorted(project_path.rglob("Package.swift")):
        sp = str(manifest)
        if "/DerivedData/" in sp or "/.build/" in sp or "/SourcePackages/" in sp:
            continue
        module_root = manifest.parent
        sources_root = module_root / "Sources"
        if sources_root.is_dir():
            count = sum(1 for _ in sources_root.rglob("*.swift"))
        else:
            count = sum(1 for _ in module_root.rglob("*.swift") if _ != manifest)
        out.append(
            LocalModule(
                name=module_root.name,
                path=str(module_root),
                source_count=count,
            )
        )
    return out


def _enumerate_xcodeproj_remote_refs(project_path: pathlib.Path) -> list[str]:
    """Return identities of every XCRemoteSwiftPackageReference in *.xcodeproj.

    Identities are resolved as the last path component of the
    repositoryURL (matching how Xcode displays them in the Package
    Dependencies tab).
    """

    identities: list[str] = []
    for pbx_path in sorted(project_path.rglob("project.pbxproj")):
        if "/Pods/" in str(pbx_path):
            continue
        try:
            xml_bytes = _convert_pbxproj_to_xml(pbx_path)
            plist = plistlib.loads(xml_bytes)
        except (subprocess.CalledProcessError, plistlib.InvalidFileException):
            continue
        objects = plist.get("objects", {}) if isinstance(plist, dict) else {}
        if not isinstance(objects, dict):
            continue
        for obj in objects.values():
            if not isinstance(obj, dict):
                continue
            if obj.get("isa") != "XCRemoteSwiftPackageReference":
                continue
            repo_url = str(obj.get("repositoryURL", ""))
            ident = repo_url.rstrip("/").rsplit("/", 1)[-1]
            ident = re.sub(r"\.git$", "", ident)
            if ident:
                identities.append(ident)
    return sorted(set(identities))


def package_graph(
    project_path: pathlib.Path,
    platform: str = "ios",
) -> PackageGraph:
    """Return the SPM graph (pins + local modules + xcodeproj remote refs)."""

    require_ios(platform)
    pins: list[PackagePin] = []
    seen: set[tuple[str, str]] = set()
    for resolved_path in _walk_package_resolved_files(project_path):
        for pin in _parse_package_resolved(resolved_path):
            key = (pin.name, pin.location)
            if key in seen:
                continue
            seen.add(key)
            pins.append(pin)

    local_modules = _enumerate_local_modules(project_path)
    remote_refs = _enumerate_xcodeproj_remote_refs(project_path)

    return PackageGraph(
        pins=tuple(pins),
        local_modules=tuple(local_modules),
        remote_xcodeproj_refs=tuple(remote_refs),
    )


# --- fix surface — Phase A stub -------------------------------------------


def apply_fix(project_path, finding_id, patch, branch_name, platform="ios"):
    """Stub for Phase A: applies an approved patch on a fresh git branch."""
    raise NotImplementedError(
        "apply_fix ships in Phase A (ios-build-fix)."
    )


def adapter_label() -> str:
    """Identifier used in benchmark artifact ``project.build_system`` field."""
    return "xcode"


if __name__ == "__main__":  # pragma: no cover — module is library-first
    print("xcode_adapter: import as scripts.adapters.xcode_adapter", file=sys.stderr)
    raise SystemExit(2)
