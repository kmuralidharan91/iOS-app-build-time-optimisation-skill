"""Xcode adapter — measurement parts for ios-build-measure (Phase A).

The diagnose-side methods (show_build_settings, script_phases,
package_graph) and the fix-side method (apply_fix) raise
NotImplementedError until chats 2 and 4 fill them in.

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

import os
import pathlib
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

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


# --- diagnose / fix surface — Phase A/4 stubs ------------------------------

def show_build_settings(project_path, scheme, configuration, platform="ios"):
    """Stub for Phase A: returns ``xcodebuild -showBuildSettings -json`` output."""
    raise NotImplementedError(
        "show_build_settings ships in Phase A (ios-build-diagnose). "
        "See docs/PLAN.md 'Execution phasing' Phase A row."
    )


def script_phases(project_path, platform="ios"):
    """Stub for Phase A: returns the parsed PBXShellScriptBuildPhase list."""
    raise NotImplementedError(
        "script_phases ships in Phase A (ios-build-diagnose)."
    )


def package_graph(project_path, platform="ios"):
    """Stub for Phase A: returns the SPM dependency tree."""
    raise NotImplementedError(
        "package_graph ships in Phase A (ios-build-diagnose)."
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
