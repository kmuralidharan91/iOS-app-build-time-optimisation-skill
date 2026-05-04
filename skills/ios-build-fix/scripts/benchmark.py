#!/usr/bin/env python3
"""ios-build-measure CLI: run repeatable benchmarks and emit a JSON artifact.

Workflow per invocation:

1. Detect the build system at ``--project-path`` (defaults to cwd).
2. Run ``--repeats`` clean and/or incremental builds via the chosen
   adapter, timing each with a monotonic clock.
3. Compute per-build-type :class:`TypeSummary` rollups including
   ``spread_seconds`` / ``spread_percent`` / ``high_variance``
   relative to ``--variance-threshold`` (default 10).
4. Derive ``critical_path`` per run via scripts/critical_path.py.
5. Compare against ``<project>/.build-history/`` for regression flags.
6. Write the artifact to ``--output-dir`` and persist a copy under
   ``<project>/.build-history/runs/`` for cross-run history.

When a build type's ``high_variance`` flag fires AND ``--repeats`` is
below 5, a Warning is emitted to stderr and appended to the artifact's
``notes`` list — re-implementation of the PR-#3 spec described in
~/.claude/plans/claude-s-plan-explain-the-xcode-build-wi-cryptic-haven.md
lines 536–595, recreated clean-room.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from adapters import (  # noqa: E402
    TimedBuild,
    TypeSummary,
    detect_build_system,
    load_adapter,
    require_ios,
)
from critical_path import critical_path_to_dict, derive_critical_path  # noqa: E402
from history_db import (  # noqa: E402
    regression_check,
    regression_report_to_dict,
    write_run,
)


SCHEMA_VERSION = "1.0.0"
TOOL_NAME = "ios-build-measure"
TOOL_VERSION = "0.1.0"
KNOWN_BUILD_TYPES = ("clean", "incremental")


def parse_cli(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="benchmark.py",
        description=(
            "Benchmark iOS build wall-clock across Xcode / Tuist / Bazel "
            "projects, with critical-path attribution and cross-run "
            "regression history."
        ),
    )
    parser.add_argument(
        "--project-path", type=pathlib.Path, default=pathlib.Path.cwd(),
        help="Project root directory (default: current working directory).",
    )
    parser.add_argument(
        "--scheme", default=None,
        help="Xcode/Tuist scheme name. Required for Xcode/Tuist projects.",
    )
    parser.add_argument(
        "--configuration", default="Debug",
        help='Build configuration name (default: "Debug"). Free string — '
             'REDACTED uses "Distribution" for release-equivalent.',
    )
    parser.add_argument(
        "--destination",
        default="generic/platform=iOS Simulator",
        help='xcodebuild -destination string '
             '(default: "generic/platform=iOS Simulator").',
    )
    parser.add_argument(
        "--platform", default="ios",
        help='Apple platform (default: "ios"). v1 enforces "ios"; '
             "v2 will add macOS / watchOS / tvOS / visionOS.",
    )
    parser.add_argument(
        "--repeats", type=int, default=3,
        help="Number of repeats per build type (default: 3). Variance "
             "warning recommends >=5 when spread exceeds threshold.",
    )
    parser.add_argument(
        "--build-types", default="clean,incremental",
        help="Comma-separated list of build types to measure. "
             "Allowed: clean,incremental. Default: clean,incremental.",
    )
    parser.add_argument(
        "--touch-file", type=pathlib.Path, default=None,
        help="File to touch between incremental repeats. Required when "
             "incremental is in --build-types.",
    )
    parser.add_argument(
        "--variance-threshold", type=float, default=10.0,
        help="Spread-as-percent-of-median threshold above which "
             "high_variance fires (default: 10.0).",
    )
    parser.add_argument(
        "--regression-window", type=int, default=5,
        help="Number of recent runs (same branch) to compare against "
             "for regression detection (default: 5).",
    )
    parser.add_argument(
        "--output-dir", type=pathlib.Path, required=True,
        help="Directory where the JSON artifact + per-run logs/xcresults "
             "are written. Created if missing.",
    )
    parser.add_argument(
        "--extra-xcodebuild-arg", action="append", default=[],
        help="Extra argument forwarded to xcodebuild (repeatable).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_cli(argv)
    require_ios(args.platform)

    project_path = args.project_path.expanduser().resolve()
    if not project_path.is_dir():
        print(f"error: --project-path is not a directory: {project_path}",
              file=sys.stderr)
        return 2

    build_types = _parse_build_types(args.build_types)
    if not build_types:
        print(f"error: --build-types must contain at least one of "
              f"{KNOWN_BUILD_TYPES}", file=sys.stderr)
        return 2

    if "incremental" in build_types and args.touch_file is None:
        print("error: --touch-file is required when --build-types includes "
              "incremental", file=sys.stderr)
        return 2

    bs = detect_build_system(project_path)
    adapter = load_adapter(bs)

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    runs: dict[str, list[dict[str, Any]]] = {bt: [] for bt in build_types}
    critical_paths: dict[str, dict[str, Any]] = {}

    for build_type in build_types:
        for repeat_index in range(args.repeats):
            print(f"[ios-build-measure] {build_type} repeat "
                  f"{repeat_index + 1}/{args.repeats}…",
                  flush=True, file=sys.stderr)
            timed = adapter.measure(
                project_path=project_path,
                scheme=args.scheme,
                configuration=args.configuration,
                destination=args.destination,
                platform=args.platform,
                touch_file=args.touch_file,
                kind=build_type,
                output_dir=output_dir / f"runs-{build_type}",
                repeat_index=repeat_index,
                extra_xcodebuild_args=args.extra_xcodebuild_arg,
            )
            runs[build_type].append(_timed_build_to_dict(timed))
            if timed.exit_code != 0:
                print(f"  exit_code={timed.exit_code} (continuing — "
                      f"benchmark records the failure but does not abort)",
                      file=sys.stderr)

        # Derive critical path from the most recent successful run of the
        # type. We use the LAST run because it has the warmest caches and
        # most realistic mid-session timing.
        last_run = runs[build_type][-1]
        bundle_path = (
            pathlib.Path(last_run["result_bundle_path"])
            if last_run.get("result_bundle_path") else None
        )
        log_path = pathlib.Path(last_run["stdout_log_path"])
        cp = derive_critical_path(bundle_path, log_path)
        critical_paths[build_type] = critical_path_to_dict(cp)

    summaries = {
        bt: dataclasses.asdict(_compute_summary(runs[bt], args.variance_threshold))
        for bt in build_types
    }

    notes: list[str] = []
    for bt, summary in summaries.items():
        if summary["high_variance"] and args.repeats < 5:
            warning = (
                f"NOTE: {bt} measurement is noisy — observed spread is "
                f"{summary['spread_percent']:.2f}% of the median, above the "
                f"{args.variance_threshold:.1f}% threshold. Re-run with "
                f"--repeats 5 to narrow the variance window."
            )
            print(warning, file=sys.stderr)
            notes.append(warning)

    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": {
            "path": str(project_path),
            "build_system": adapter.adapter_label(),
            "git_sha": _git_head_sha(project_path),
            "git_branch": _git_branch(project_path),
            "platform": args.platform,
        },
        "configuration": {
            "scheme": args.scheme,
            "configuration": args.configuration,
            "destination": args.destination,
            "repeats": args.repeats,
            "build_types": list(build_types),
            "variance_threshold_percent": args.variance_threshold,
            "extra_xcodebuild_args": list(args.extra_xcodebuild_arg),
        },
        "runs": runs,
        "summary": summaries,
        "critical_path": critical_paths,
        "history": {},  # filled below
        "notes": notes,
    }

    # Regression check is computed BEFORE write_run so we don't compare
    # against ourselves.
    report = regression_check(
        project_path,
        artifact,
        window=args.regression_window,
        variance_threshold_percent=args.variance_threshold,
    )
    artifact["history"] = regression_report_to_dict(report)
    if report.has_regression:
        msg = (
            f"Warning: regression detected vs last {report.window_used} "
            f"runs: {report.deltas_percent}"
        )
        print(msg, file=sys.stderr)
        artifact["notes"].append(msg)

    artifact_path = output_dir / "measurement.json"
    artifact_path.write_text(json.dumps(artifact, indent=2, sort_keys=True))
    print(f"wrote: {artifact_path}", file=sys.stderr)

    history_run_path = write_run(project_path, artifact)
    print(f"history: {history_run_path}", file=sys.stderr)
    return 0


# --- helpers --------------------------------------------------------------


def _parse_build_types(raw: str) -> list[str]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: list[str] = []
    for p in parts:
        if p not in KNOWN_BUILD_TYPES:
            raise SystemExit(
                f"error: unknown build type {p!r}; "
                f"allowed: {KNOWN_BUILD_TYPES}"
            )
        if p not in out:
            out.append(p)
    return out


def _timed_build_to_dict(timed: TimedBuild) -> dict[str, Any]:
    return {
        "kind": timed.kind,
        "duration_seconds": round(timed.duration_seconds, 3),
        "exit_code": timed.exit_code,
        "stdout_log_path": timed.stdout_log_path,
        "result_bundle_path": timed.result_bundle_path,
        "started_at": timed.started_at,
        "finished_at": timed.finished_at,
    }


def _compute_summary(
    type_runs: list[dict[str, Any]], variance_threshold_percent: float
) -> TypeSummary:
    durations = [
        float(r["duration_seconds"])
        for r in type_runs
        if r.get("exit_code") == 0
    ]
    if not durations:
        return TypeSummary(
            count=0, min_seconds=0.0, max_seconds=0.0,
            median_seconds=0.0, mean_seconds=0.0,
            spread_seconds=0.0, spread_percent=0.0, high_variance=False,
        )

    minimum = min(durations)
    maximum = max(durations)
    median = statistics.median(durations)
    mean = statistics.mean(durations)
    spread = maximum - minimum
    spread_percent = (spread / median * 100.0) if median > 0 else 0.0
    return TypeSummary(
        count=len(durations),
        min_seconds=round(minimum, 3),
        max_seconds=round(maximum, 3),
        median_seconds=round(median, 3),
        mean_seconds=round(mean, 3),
        spread_seconds=round(spread, 3),
        spread_percent=round(spread_percent, 3),
        high_variance=spread_percent > variance_threshold_percent
        and len(durations) >= 2
        and median > 0,
    )


def _git_head_sha(project_path: pathlib.Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_path, capture_output=True, text=True, check=True,
        )
        return result.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _git_branch(project_path: pathlib.Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=project_path, capture_output=True, text=True, check=True,
        )
        branch = result.stdout.strip()
        if branch and branch != "HEAD":
            return branch
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    # Detached HEAD (common in worktrees / CI): try to find a remote
    # branch whose tip matches our HEAD SHA. Returns the first match,
    # stripped of the remote prefix (e.g., "origin/develop" -> "develop").
    try:
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_path, capture_output=True, text=True, check=True,
        ).stdout.strip()
        for_each = subprocess.run(
            ["git", "for-each-ref", "--points-at", head_sha,
             "--format=%(refname:short)", "refs/remotes/"],
            cwd=project_path, capture_output=True, text=True, check=True,
        )
        for line in for_each.stdout.splitlines():
            line = line.strip()
            if not line or line.endswith("/HEAD") or "/" not in line:
                # "/" not in line catches the bare remote-name short ref
                # (e.g. "origin" rendered from refs/remotes/origin/HEAD).
                continue
            # "origin/develop" -> "develop"; first match wins
            return line.split("/", 1)[-1]
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return None


if __name__ == "__main__":
    raise SystemExit(main())
