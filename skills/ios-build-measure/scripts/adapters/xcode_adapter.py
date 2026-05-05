"""Xcode adapter — measurement + diagnose surface for the build skills.

The measurement parts (``measure``, ``time_one_build``, ``run_clean``)
ship in Phase A for ``ios-build-measure``. The diagnose-side methods
(``show_build_settings``, ``script_phases``, ``package_graph``) ship
in Phase A: ``show_build_settings`` invokes
``xcodebuild -showBuildSettings -json``; ``script_phases`` parses
``project.pbxproj`` plists for ``PBXShellScriptBuildPhase`` entries;
``package_graph`` walks ``Package.resolved`` and ``Package.swift``
manifests. The fix-side method ``apply_fix`` is the v0 contract
placeholder — Phase A superseded it with the ``scripts/fixers/``
module design (see ``scripts/fix.py``); the stub is retained for
the AGENTS.md adapter-contract signature only and is not called by
any production code.

Wall-clock-only measurement strategy:

1. Build the xcodebuild argv with -showBuildTimingSummary plus a
   per-run -resultBundlePath under the output directory. Stdout is
   tee'd to a log file so the timing-summary fallback parser can
   read it without re-running the build.
2. Time the subprocess wall-clock with a monotonic clock; do not
   trust xcodebuild's own self-reported number — that one excludes
   spawn / cleanup overhead.
3. Capture .xcresult bundle path so scripts/critical_path.py can
   parse the per-target build summary via xcrun xcresulttool.
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
from datetime import datetime, timezone

from . import (
    LocalModule,
    PackageGraph,
    Pin,
    ScriptPhase,
    TimedBuild,
    require_ios,
)


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


# --- diagnose / fix surface — Phase A/4 stubs ------------------------------

def show_build_settings(
    project_path: pathlib.Path,
    scheme: str | None,
    configuration: str,
    platform: str = "ios",
) -> dict[str, str]:
    """Return the merged ``xcodebuild -showBuildSettings -json`` dict.

    Invokes ``xcodebuild -showBuildSettings -json`` on the resolved
    workspace/project (per :func:`find_workspace_or_project`), parses
    the JSON array of ``{target, action, buildSettings}`` entries, and
    merges the per-target ``buildSettings`` dicts into one flat
    ``dict[str, str]`` using the same loop shape as
    ``_load_resolved_settings_dump`` in ``scripts/diagnose.py`` so the
    live and dump paths produce identical inputs to
    ``analyzers/build_setting.py``.

    Returns an empty dict (and logs to stderr) on the failure modes
    ``scripts/diagnose.py`` already handles — missing xcodebuild
    binary, network down, non-JSON output, or non-zero exit. Diagnose
    then short-circuits the build-setting findings with the same note
    the ``--skip-xcodebuild`` path emits.
    """

    require_ios(platform)
    flag, container = find_workspace_or_project(project_path)
    argv: list[str] = [
        "xcodebuild", flag, str(container),
        "-showBuildSettings", "-json",
        "-configuration", configuration,
    ]
    if scheme:
        argv.extend(["-scheme", scheme])

    try:
        completed = subprocess.run(
            argv,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(
            f"[xcode_adapter.show_build_settings] xcodebuild invocation failed: {exc}",
            file=sys.stderr,
        )
        return {}

    if completed.returncode != 0:
        print(
            f"[xcode_adapter.show_build_settings] xcodebuild exit "
            f"{completed.returncode}: {completed.stderr.strip()[:200]}",
            file=sys.stderr,
        )
        return {}

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        print(
            f"[xcode_adapter.show_build_settings] non-JSON xcodebuild output: {exc}",
            file=sys.stderr,
        )
        return {}

    merged: dict[str, str] = {}
    if isinstance(payload, list):
        for entry in payload:
            settings = entry.get("buildSettings", {}) if isinstance(entry, dict) else {}
            for key, value in settings.items():
                merged[str(key)] = str(value)
    elif isinstance(payload, dict):
        merged = {str(k): str(v) for k, v in payload.items()}
    return merged


_SKIP_DIR_PARTS = ("DerivedData", ".build", "Pods", ".git", "node_modules")


def _iter_pbxproj_files(project_path: pathlib.Path) -> list[pathlib.Path]:
    """Find every ``project.pbxproj`` under ``project_path``.

    Skips well-known build / cache / vendor directories so we don't
    parse derived projects that were generated for a third party.
    """

    out: list[pathlib.Path] = []
    for hit in project_path.rglob("project.pbxproj"):
        parts = hit.parts
        if any(skip in parts for skip in _SKIP_DIR_PARTS):
            continue
        out.append(hit)
    return out


def _load_pbxproj(pbxproj: pathlib.Path) -> dict | None:
    """Read a pbxproj as a dict.

    Tries :func:`plistlib.loads` directly first (Xcode often writes the
    plist in XML or binary form). When the file is in the older ASCII
    plist dialect, falls back to ``plutil -convert xml1 -o - <path>``
    and parses that.
    """

    raw = pbxproj.read_bytes()
    try:
        return plistlib.loads(raw)
    except plistlib.InvalidFileException:
        pass
    try:
        completed = subprocess.run(
            ["plutil", "-convert", "xml1", "-o", "-", str(pbxproj)],
            check=True,
            capture_output=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired) as exc:
        print(
            f"[xcode_adapter.script_phases] plutil convert failed for "
            f"{pbxproj}: {exc}",
            file=sys.stderr,
        )
        return None
    try:
        return plistlib.loads(completed.stdout)
    except plistlib.InvalidFileException as exc:
        print(
            f"[xcode_adapter.script_phases] plistlib still can't parse "
            f"{pbxproj}: {exc}",
            file=sys.stderr,
        )
        return None


def _coerce_truthy(value) -> bool:
    """Normalise pbxproj truthy literals (``YES``, ``1``, ``true``, etc.)."""

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("yes", "true", "1")
    return False


def _string_tuple(value) -> tuple[str, ...]:
    """Return ``value`` coerced into a tuple of strings; handles missing keys."""

    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return (str(value),)


def script_phases(
    project_path: pathlib.Path,
    platform: str = "ios",
) -> list[ScriptPhase]:
    """Return every ``PBXShellScriptBuildPhase`` reachable under the project.

    Walks every ``project.pbxproj`` under ``project_path`` (skipping
    derived/cache dirs), parses it as a plist, and emits one
    :class:`ScriptPhase` per shell-script build phase, attributing it
    to the owning ``PBXNativeTarget`` by reverse-traversing the target's
    ``buildPhases`` reference list.

    Missing ``inputPaths`` / ``outputPaths`` keys are coerced to empty
    tuples so ``analyzers/script_phase.py`` fires the
    missing-output-declarations rule against the same shape it expects
    from Phase A's known-good ground truth.
    """

    require_ios(platform)
    out: list[ScriptPhase] = []

    for pbxproj in _iter_pbxproj_files(project_path):
        plist = _load_pbxproj(pbxproj)
        if not plist:
            continue
        objects = plist.get("objects") or {}
        if not isinstance(objects, dict):
            continue

        # First pass: every target id -> target name (via PBXNativeTarget).
        target_name_by_phase_id: dict[str, str] = {}
        for obj in objects.values():
            if not isinstance(obj, dict):
                continue
            if obj.get("isa") not in ("PBXNativeTarget", "PBXAggregateTarget", "PBXLegacyTarget"):
                continue
            target_name = str(obj.get("name") or obj.get("productName") or "<unknown>")
            for phase_id in obj.get("buildPhases") or ():
                if isinstance(phase_id, str):
                    target_name_by_phase_id[phase_id] = target_name

        # Second pass: every shell-script phase, attributed to its owner.
        for phase_id, obj in objects.items():
            if not isinstance(obj, dict):
                continue
            if obj.get("isa") != "PBXShellScriptBuildPhase":
                continue
            target = target_name_by_phase_id.get(phase_id, "<orphan>")
            name = str(obj.get("name") or "Run Script")
            script_body = str(obj.get("shellScript") or "")
            input_paths = _string_tuple(obj.get("inputPaths"))
            output_paths = _string_tuple(obj.get("outputPaths"))
            always_out_of_date = _coerce_truthy(obj.get("alwaysOutOfDate"))
            out.append(ScriptPhase(
                target=target,
                name=name,
                script=script_body,
                input_paths=input_paths,
                output_paths=output_paths,
                always_out_of_date=always_out_of_date,
                pbxproj_path=str(pbxproj),
            ))
    return out


def _read_package_resolved(path: pathlib.Path) -> list[Pin]:
    """Parse one Package.resolved file into a list of Pin objects.

    Handles v1 (object.pins[] with ``package`` field) and v2/v3
    (top-level ``pins[]`` with ``identity``) shapes.
    """

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"[xcode_adapter.package_graph] could not parse {path}: {exc}",
            file=sys.stderr,
        )
        return []

    pin_entries: list[dict] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("pins"), list):
            pin_entries = [p for p in payload["pins"] if isinstance(p, dict)]
        else:
            obj = payload.get("object")
            if isinstance(obj, dict) and isinstance(obj.get("pins"), list):
                pin_entries = [p for p in obj["pins"] if isinstance(p, dict)]

    pins: list[Pin] = []
    for entry in pin_entries:
        identity = entry.get("identity") or entry.get("package") or ""
        state = entry.get("state") or {}
        if not isinstance(state, dict):
            state = {}
        pins.append(Pin(
            name=str(identity),
            version=(str(state["version"]) if state.get("version") else None),
            revision=(str(state["revision"]) if state.get("revision") else None),
            branch=(str(state["branch"]) if state.get("branch") else None),
            location=str(entry.get("location") or ""),
            source_resolved_path=str(path),
        ))
    return pins


def _count_swift_sources(package_dir: pathlib.Path) -> int:
    """Count ``*.swift`` under the package's source roots, excluding tests."""

    count = 0
    sources_root = package_dir / "Sources"
    roots: list[pathlib.Path] = []
    if sources_root.is_dir():
        roots.append(sources_root)
    else:
        # Some local packages are flat (no Sources/ dir); fall back to
        # the package root excluding Tests/.
        roots.append(package_dir)
    for root in roots:
        for hit in root.rglob("*.swift"):
            parts = hit.parts
            if "Tests" in parts or ".build" in parts:
                continue
            count += 1
    return count


def package_graph(
    project_path: pathlib.Path,
    platform: str = "ios",
) -> PackageGraph:
    """Return the merged SPM graph reachable under ``project_path``.

    Walks every ``Package.resolved`` under the project tree (skipping
    derived/cache dirs), aggregates pins de-duplicated by
    ``(name, source_resolved_path)``, then walks every local
    ``Package.swift`` manifest to count its Swift source files for the
    oversized-module rule.
    """

    require_ios(platform)
    pins: list[Pin] = []
    seen_pin_keys: set[tuple[str, str]] = set()
    for resolved in project_path.rglob("Package.resolved"):
        if any(skip in resolved.parts for skip in _SKIP_DIR_PARTS):
            continue
        for pin in _read_package_resolved(resolved):
            key = (pin.name.lower(), pin.source_resolved_path)
            if key in seen_pin_keys:
                continue
            seen_pin_keys.add(key)
            pins.append(pin)

    local_modules: list[LocalModule] = []
    seen_module_paths: set[str] = set()
    for manifest in project_path.rglob("Package.swift"):
        if any(skip in manifest.parts for skip in _SKIP_DIR_PARTS):
            continue
        package_dir = manifest.parent
        package_dir_str = str(package_dir)
        if package_dir_str in seen_module_paths:
            continue
        seen_module_paths.add(package_dir_str)
        # Use the directory name as the canonical SPM identity. REDACTED's
        # `REDACTED` directory contains a `Package.swift` whose name
        # field is `REDACTEDREDACTED`; Phase A's known-good ground truth
        # surfaces the directory name (`REDACTED`), and the diagnose
        # rule's evidence path is the directory anyway.
        name = package_dir.name
        source_count = _count_swift_sources(package_dir)
        local_modules.append(LocalModule(
            name=name,
            path=package_dir_str,
            source_count=source_count,
        ))
    return PackageGraph(
        pins=tuple(pins),
        local_modules=tuple(local_modules),
    )


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
