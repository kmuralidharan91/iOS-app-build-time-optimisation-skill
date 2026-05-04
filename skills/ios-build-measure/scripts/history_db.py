"""Cross-run regression history for ios-build-measure.

Stores one JSON file per measurement under ``<project>/.build-history/runs/``,
keyed by the measurement timestamp + git SHA. ``index.json`` is a
roll-up cache (medians-of-medians per branch) recomputed on every
``write_run`` so the diagnose / fix skills can read recent history
without scanning the runs directory.

Regression check: compare the current artifact's medians against the
previous N=window runs on the same branch. A regression is flagged
when the current median exceeds the historical median by more than
``variance_threshold`` percent for any build type. The ``deltas`` map
in the returned :class:`RegressionReport` gives the exact percentages
so the developer can see why the flag fired.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import pathlib
import re
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


HISTORY_DIRNAME = ".build-history"
RUNS_DIRNAME = "runs"
INDEX_FILENAME = "index.json"

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


@dataclasses.dataclass(frozen=True)
class RegressionReport:
    has_regression: bool
    deltas_percent: dict[str, float]
    window_used: int
    threshold_percent: float
    notes: list[str]


def history_dir(project_path: pathlib.Path) -> pathlib.Path:
    return project_path / HISTORY_DIRNAME


def runs_dir(project_path: pathlib.Path) -> pathlib.Path:
    return history_dir(project_path) / RUNS_DIRNAME


def write_run(
    project_path: pathlib.Path, artifact: dict[str, Any]
) -> pathlib.Path:
    """Persist one benchmark artifact under ``<project>/.build-history/runs/``."""

    runs = runs_dir(project_path)
    runs.mkdir(parents=True, exist_ok=True)

    git_sha = _short_sha(artifact)
    branch = _branch(artifact)
    when = _generated_at(artifact)
    scheme = (artifact.get("configuration") or {}).get("scheme") or "default"
    config = (artifact.get("configuration") or {}).get("configuration") or "Debug"

    safe_when = when.replace(":", "-")
    safe_scheme = _safe_segment(scheme)
    safe_config = _safe_segment(config)
    name = f"{safe_when}__sha-{git_sha}__{safe_scheme}-{safe_config}.json"
    out_path = runs / name
    out_path.write_text(json.dumps(artifact, indent=2, sort_keys=True))

    _refresh_index(project_path, branch_hint=branch)
    return out_path


def regression_check(
    project_path: pathlib.Path,
    current: dict[str, Any],
    window: int = 5,
    variance_threshold_percent: float = 10.0,
) -> RegressionReport:
    """Compare the current artifact's medians against the last ``window`` runs."""

    branch = _branch(current)
    runs = _load_recent_runs(project_path, branch=branch, window=window)
    if not runs:
        return RegressionReport(
            has_regression=False,
            deltas_percent={},
            window_used=0,
            threshold_percent=variance_threshold_percent,
            notes=[
                f"no historical runs found on branch={branch!r}; "
                f"this is the first measurement — no regression baseline yet"
            ],
        )

    deltas: dict[str, float] = {}
    flagged = False
    for build_type in ("clean", "incremental"):
        current_median = _median_for(current, build_type)
        if current_median is None:
            continue
        historical_medians = [
            m for m in (_median_for(r, build_type) for r in runs) if m is not None
        ]
        if not historical_medians:
            continue
        baseline = _median_of(historical_medians)
        if baseline <= 0:
            continue
        delta_pct = ((current_median - baseline) / baseline) * 100.0
        deltas[build_type] = round(delta_pct, 2)
        if delta_pct > variance_threshold_percent:
            flagged = True

    return RegressionReport(
        has_regression=flagged,
        deltas_percent=deltas,
        window_used=len(runs),
        threshold_percent=variance_threshold_percent,
        notes=[],
    )


def regression_report_to_dict(report: RegressionReport) -> dict[str, Any]:
    return {
        "regression_detected": report.has_regression,
        "deltas_percent": dict(report.deltas_percent),
        "window_used": report.window_used,
        "threshold_percent": report.threshold_percent,
        "notes": list(report.notes),
    }


# --- helpers --------------------------------------------------------------


def _short_sha(artifact: dict[str, Any]) -> str:
    project = artifact.get("project") or {}
    sha = project.get("git_sha") or "nosha"
    if isinstance(sha, str) and _SHA_RE.match(sha):
        return sha[:8]
    return "nosha"


def _branch(artifact: dict[str, Any]) -> str:
    project = artifact.get("project") or {}
    branch = project.get("git_branch")
    if isinstance(branch, str) and branch:
        return branch
    return "unknown"


def _generated_at(artifact: dict[str, Any]) -> str:
    when = artifact.get("generated_at")
    if isinstance(when, str) and when:
        return when
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _safe_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)[:48] or "default"


def _median_for(artifact: dict[str, Any], build_type: str) -> float | None:
    summary = (artifact.get("summary") or {}).get(build_type)
    if not isinstance(summary, dict):
        return None
    median = summary.get("median_seconds")
    if isinstance(median, (int, float)):
        return float(median)
    return None


def _median_of(values: list[float]) -> float:
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        return sorted_vals[n // 2]
    return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0


def _load_recent_runs(
    project_path: pathlib.Path, branch: str, window: int
) -> list[dict[str, Any]]:
    runs = runs_dir(project_path)
    if not runs.is_dir():
        return []
    files = sorted(runs.glob("*.json"), reverse=True)
    out: list[dict[str, Any]] = []
    for path in files:
        try:
            artifact = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if _branch(artifact) != branch:
            continue
        out.append(artifact)
        if len(out) >= window:
            break
    return out


def _refresh_index(
    project_path: pathlib.Path, branch_hint: str | None = None
) -> None:
    runs = runs_dir(project_path)
    if not runs.is_dir():
        return
    by_branch: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(runs.glob("*.json")):
        try:
            artifact = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        branch = _branch(artifact)
        entry = {
            "file": path.name,
            "git_sha": _short_sha(artifact),
            "generated_at": _generated_at(artifact),
            "median_clean_seconds": _median_for(artifact, "clean"),
            "median_incremental_seconds": _median_for(artifact, "incremental"),
        }
        by_branch.setdefault(branch, []).append(entry)

    index_payload = {
        "schema": "ios-build-measure-history-index/1.0.0",
        "updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "by_branch": {
            branch: sorted(entries, key=lambda e: e["generated_at"], reverse=True)
            for branch, entries in by_branch.items()
        },
    }
    history_dir(project_path).mkdir(parents=True, exist_ok=True)
    (history_dir(project_path) / INDEX_FILENAME).write_text(
        json.dumps(index_payload, indent=2, sort_keys=True)
    )
