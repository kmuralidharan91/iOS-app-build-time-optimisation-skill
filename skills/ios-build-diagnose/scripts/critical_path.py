"""Critical-path attribution for Xcode and Bazel builds.

**Xcode path (v1.0).** Parses the ``Build Timing Summary`` block
emitted by ``xcodebuild -showBuildTimingSummary`` and turns the
task-class aggregates (e.g. ``SwiftCompile (2465 tasks) | 2538.981
seconds``) into a populated ``critical_path`` field on the benchmark
artifact. Each task class is reported as one node ranked by duration;
``longest_chain_seconds`` is the duration of the dominant task class.

**Bazel path (v1.2).** Parses the chrome-trace JSON profile written by
``bazelisk build --profile=<path>``. ``cat=critical path component``
events are extracted in trace order as the authoritative critical path
(Bazel computes this server-side from the action DAG); when the
profile is too small to surface multiple critical-path components,
falls back to ``cat=action processing`` events ranked by wall-clock as
a task-list attribution. See
https://bazel.build/advanced/performance/json-trace-profile for the
profile schema.

Parsing inputs (in order of preference):

1. The xcodebuild stdout log (Xcode). Contains the ``Build Timing
   Summary`` block verbatim.
2. The Bazel chrome-trace profile sibling of the log (``<log>``
   replaced by ``<log_basename-without-.log>-profile.json``). Detected
   by the ``otherData.bazel_version`` marker at the top of the JSON.
3. The ``.xcresult`` bundle (Xcode fallback). Probed via
   ``xcrun xcresulttool get --legacy``.

If none yield data, ``critical_path`` is returned with ``method=None``
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


def _bazel_profile_path_for_log(log_path: pathlib.Path) -> pathlib.Path:
    """Return the expected Bazel chrome-trace profile sibling of ``log_path``.

    ``bazel_adapter.measure`` writes ``build-<kind>-<repeat>.log`` and
    ``build-<kind>-<repeat>-profile.json`` side by side.
    """

    return log_path.with_name(log_path.stem + "-profile.json")


def parse_bazel_profile_critical_path(
    profile_path: pathlib.Path,
    max_action_nodes: int = 20,
) -> tuple[list[CriticalPathNode], float, str] | None:
    """Parse a Bazel chrome-trace JSON profile into a critical-path attribution.

    Preference order inside the profile:

    1. Events with ``cat == "critical path component"``. Bazel computes
       these server-side from the action DAG; their wall-clock sum is the
       authoritative critical-path length. They arrive in trace order
       (which mirrors graph order), so we keep that ordering.
    2. Events with ``cat == "action processing"`` ranked by ``dur``. Used
       only when (1) yields fewer than 2 entries (very small builds), so
       the surfaced attribution remains useful for tiny smoke targets.

    Returns ``(nodes, longest_chain_seconds, method)`` or ``None`` when
    the file is unreadable or contains no usable events.
    """

    try:
        text = profile_path.read_text()
    except OSError:
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None
    if "bazel_version" not in (data.get("otherData") or {}):
        return None
    events = data.get("traceEvents", [])
    if not isinstance(events, list):
        return None

    def _us_to_seconds(us: int | float) -> float:
        return float(us) / 1_000_000.0

    cp_events: list[tuple[str, float]] = []
    action_events: list[tuple[str, float]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("ph") != "X":
            continue
        dur = event.get("dur")
        name = event.get("name")
        cat = event.get("cat")
        if dur is None or not isinstance(name, str):
            continue
        seconds = _us_to_seconds(dur)
        if cat == "critical path component":
            # Strip the "action '...'" wrapper Bazel emits so the node
            # surface is comparable to xcodebuild's task classes.
            stripped = name
            if stripped.startswith("action '") and stripped.endswith("'"):
                stripped = stripped[len("action '") : -1]
            cp_events.append((stripped, seconds))
        elif cat == "action processing":
            action_events.append((name, seconds))

    if len(cp_events) >= 2:
        nodes = [
            CriticalPathNode(
                target=name,
                duration_seconds=seconds,
                depth=rank,
                dominant_task=name,
                predecessors=[],
            )
            for rank, (name, seconds) in enumerate(cp_events)
        ]
        return nodes, sum(s for _, s in cp_events), "bazel-critical-path"

    # Fallback: rank action_processing events by duration. Useful for
    # smoke targets where Bazel's own critical-path output is too small.
    if action_events:
        action_events.sort(key=lambda row: row[1], reverse=True)
        top = action_events[:max_action_nodes]
        nodes = [
            CriticalPathNode(
                target=name,
                duration_seconds=seconds,
                depth=rank,
                dominant_task=name,
                predecessors=[],
            )
            for rank, (name, seconds) in enumerate(top)
        ]
        longest = top[0][1] if top else 0.0
        return nodes, longest, "bazel-action-ranked"

    return None


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

    # Bazel: parse the chrome-trace profile alongside the log. Done
    # before the xcresult fallback because the profile lives where the
    # log does (sibling file), and the xcresult fallback is xcode-only.
    if not aggregates and log_path is not None:
        profile_path = _bazel_profile_path_for_log(log_path)
        if profile_path.exists():
            bazel_result = parse_bazel_profile_critical_path(profile_path)
            if bazel_result is not None:
                nodes, longest, method = bazel_result
                notes.append(
                    "method=" + method + ": nodes are Bazel "
                    + ("critical-path components" if method == "bazel-critical-path"
                       else "action_processing events ranked by wall-clock") + " "
                    "from `bazelisk build --profile=<json>` — see "
                    "https://bazel.build/advanced/performance/json-trace-profile"
                )
                notes.append(f"timing profile source: {profile_path}")
                return CriticalPath(
                    method=method,
                    nodes=nodes,
                    longest_chain_seconds=longest,
                    notes=notes,
                )

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
