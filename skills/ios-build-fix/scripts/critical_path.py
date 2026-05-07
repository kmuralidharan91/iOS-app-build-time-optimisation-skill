"""Critical-path attribution for an Xcode build.

**Phase A scope.** This module parses the ``Build Timing Summary`` block
emitted by ``xcodebuild -showBuildTimingSummary`` and turns the
task-class aggregates (e.g. ``SwiftCompile (2465 tasks) | 2538.981
seconds``) into a populated ``critical_path`` field on the benchmark
artifact. Each task class is reported as one node ranked by duration;
``longest_chain_seconds`` is the duration of the dominant task class.

Per-target attribution and a true dependency-DAG walk would need to
parse the 14000+ ActivityLogCommandInvocationSection entries inside
the ``.xcresult`` bundle (verified against a development-time baseline:
xcresulttool 24757, schema 0.1.0, ~14865 top-level command invocations
on a single Build action — not grouped by target). Equivalent
top-level command counts on the v1.0.0 corpora ship in
``build-benchmarks/{wikipedia-ios,netnewswire}/`` xcresult bundles.
That work is deferred alongside the diagnose pass, which already
needs to walk per-target build settings and script phases.

Two parsing inputs are accepted, in order of preference:

1. The xcodebuild stdout log (already on disk because xcode_adapter
   writes it). Contains the ``Build Timing Summary`` block verbatim.
2. The ``.xcresult`` bundle. We probe it for the same Build Timing
   Summary subsection via ``xcrun xcresulttool get --legacy``; if the
   bundle is unreachable or schema-drifted, we fall back to stdout.

If both are missing, ``critical_path`` is returned with ``method=None``
and an explanatory note.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from adapters import CriticalPath, CriticalPathNode  # noqa: E402


_TIMING_LINE_RE = re.compile(
    r"^(?P<task>[A-Za-z][A-Za-z0-9_]*)\s+\((?P<count>\d+)\s+tasks?\)\s+\|\s+"
    r"(?P<sec>[\d.]+)\s+seconds?\s*$"
)


def parse_log_timing_summary(log_path: pathlib.Path) -> list[tuple[str, int, float]]:
    """Extract ``(task_class, count, seconds)`` rows from a build log."""

    if not log_path.exists():
        return []
    text = log_path.read_text(errors="replace")
    out: list[tuple[str, int, float]] = []
    for line in text.splitlines():
        match = _TIMING_LINE_RE.match(line.strip())
        if match:
            out.append((
                match.group("task"),
                int(match.group("count")),
                float(match.group("sec")),
            ))
    out.sort(key=lambda row: row[2], reverse=True)
    return out


def parse_xcresult_timing_summary(
    bundle_path: pathlib.Path,
) -> list[tuple[str, int, float]]:
    """Extract task-class aggregates from a ``.xcresult`` bundle.

    Walks the legacy ActivityLog tree until a section titled "Build
    Timing Summary" is found, then extracts its emitted text. Returns
    the same shape as :func:`parse_log_timing_summary`.
    """

    if not bundle_path.exists():
        return []

    raw_top = _xcresulttool_get(bundle_path, ref_id=None)
    if raw_top is None:
        return []

    log_id = _find_build_log_ref(raw_top)
    if log_id is None:
        return []

    raw_log = _xcresulttool_get(bundle_path, ref_id=log_id)
    if raw_log is None:
        return []

    summary_text = _find_timing_summary_text(raw_log)
    if not summary_text:
        return []

    out: list[tuple[str, int, float]] = []
    for line in summary_text.splitlines():
        match = _TIMING_LINE_RE.match(line.strip())
        if match:
            out.append((
                match.group("task"),
                int(match.group("count")),
                float(match.group("sec")),
            ))
    out.sort(key=lambda row: row[2], reverse=True)
    return out


def derive_critical_path(
    bundle_path: pathlib.Path | None,
    log_path: pathlib.Path | None,
) -> CriticalPath:
    """Top-level helper used by benchmark.py per measured run."""

    aggregates: list[tuple[str, int, float]] = []
    source = "none"
    notes: list[str] = []

    if log_path is not None and log_path.exists():
        aggregates = parse_log_timing_summary(log_path)
        if aggregates:
            source = "stdout-log"

    if not aggregates and bundle_path is not None and bundle_path.exists():
        aggregates = parse_xcresult_timing_summary(bundle_path)
        if aggregates:
            source = "xcresult-bundle"

    if not aggregates:
        return CriticalPath(
            method=None,
            nodes=[],
            longest_chain_seconds=0.0,
            notes=[
                f"no Build Timing Summary found "
                f"(log_path={log_path}, bundle_path={bundle_path})"
            ],
        )

    nodes = [
        CriticalPathNode(
            target=task_class,
            duration_seconds=seconds,
            depth=rank,
            dominant_task=task_class,
            predecessors=[],
        )
        for rank, (task_class, _count, seconds) in enumerate(aggregates)
    ]
    longest = aggregates[0][2] if aggregates else 0.0

    notes.append(
        "method=task-class-aggregate: nodes are xcodebuild task classes "
        "ranked by total wall-clock; per-target DAG attribution deferred "
        "to ios-build-diagnose — see references/critical-path-method.md"
    )
    notes.append(f"timing summary source: {source}")

    return CriticalPath(
        method="task-class-aggregate",
        nodes=nodes,
        longest_chain_seconds=longest,
        notes=notes,
    )


def critical_path_to_dict(cp: CriticalPath) -> dict[str, Any]:
    """Serialise a :class:`CriticalPath` for the JSON benchmark artifact."""
    return {
        "method": cp.method,
        "nodes": [
            {
                "target": n.target,
                "duration_seconds": n.duration_seconds,
                "depth": n.depth,
                "dominant_task": n.dominant_task,
                "predecessors": list(n.predecessors),
            }
            for n in cp.nodes
        ],
        "longest_chain_seconds": cp.longest_chain_seconds,
        "notes": list(cp.notes),
    }


# --- xcresulttool helpers -------------------------------------------------


def _xcresulttool_get(
    bundle_path: pathlib.Path, ref_id: str | None
) -> dict[str, Any] | None:
    """Run ``xcrun xcresulttool get --legacy --format json`` with optional id."""

    argv = [
        "xcrun",
        "xcresulttool",
        "get",
        "--legacy",
        "--path",
        str(bundle_path),
        "--format",
        "json",
    ]
    if ref_id is not None:
        argv.extend(["--id", ref_id])
    completed = subprocess.run(
        argv, capture_output=True, text=True, check=False
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None


def _find_build_log_ref(top: dict[str, Any]) -> str | None:
    """Locate the Build (not Clean) action's ``logRef.id`` value."""

    actions = top.get("actions", {}).get("_values", []) or []
    for action in actions:
        title = action.get("title", {}).get("_value", "")
        if title.startswith("Build "):
            log_ref = action.get("buildResult", {}).get("logRef", {})
            return log_ref.get("id", {}).get("_value")
    if actions:
        log_ref = actions[-1].get("buildResult", {}).get("logRef", {})
        return log_ref.get("id", {}).get("_value")
    return None


def _find_timing_summary_text(node: dict[str, Any]) -> str | None:
    """Walk the ActivityLog tree looking for a Build Timing Summary section."""

    title = node.get("title", {}).get("_value", "")
    if title == "Build Timing Summary":
        return _gather_emitted_text(node)
    subs = node.get("subsections", {}).get("_values", []) or []
    for sub in subs:
        found = _find_timing_summary_text(sub)
        if found:
            return found
    return None


def _gather_emitted_text(node: dict[str, Any]) -> str:
    """Concatenate the ``emittedOutput`` strings under ``node`` recursively."""

    pieces: list[str] = []
    for key in ("emittedOutput", "text", "summary"):
        value = node.get(key, {})
        if isinstance(value, dict):
            inner = value.get("_value")
            if isinstance(inner, str):
                pieces.append(inner)
    subs = node.get("subsections", {}).get("_values", []) or []
    for sub in subs:
        pieces.append(_gather_emitted_text(sub))
    return "\n".join(p for p in pieces if p)
