"""Predictor registry + dataclasses for ios-build-simulate (Phase A).

Each predictor module under this package exposes a single ``predict(findings,
context)`` entry point per rule_id. The orchestrator in ``scripts/simulate.py``
constructs a ``SimulationContext`` (parsed diagnosis.json + optional
measurement.json + project context), then for each rule_id present in
``diagnosis.findings[]`` invokes the matching predictor with the slice of
findings sharing that rule_id, collects the resulting ``RulePrediction``
records, ranks them by total predicted Δ, and serialises the artifact.

Per-rule aggregation (Phase A contract):

- A predictor consumes ALL findings sharing its rule_id and returns ONE
  ``RulePrediction``. This is intentional — when a single fix (e.g. enabling
  ``COMPILATION_CACHE_ENABLE_CACHING`` or wrapping every artifact-upload
  phase in a ``CONFIGURATION`` guard) closes multiple findings, the user
  sees one prediction, not N copies.
- Each prediction carries a ``tuning_data_point`` (required) on both clean
  and incremental axes per AGENTS.md non-negotiable principle 5.
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any, Literal


PredictionMethod = Literal[
    "measured-on-REDACTED",
    "measured-on-wikipedia",
    "heuristic",
    "literature",
]
Confidence = Literal["high", "medium", "low"]
Family = Literal["script-phase", "build-setting", "asset-catalog", "spm"]


@dataclasses.dataclass(frozen=True)
class Prediction:
    """One axis (clean OR incremental) of a rule's predicted Δ.

    A negative ``estimate_seconds`` means improvement; positive means
    regression (e.g. F4's incremental cache-invalidation cost).
    ``tuning_data_point`` is required and names the project + run that
    motivated this prediction.
    """

    method: PredictionMethod
    estimate_seconds: float | None
    min_seconds: float | None
    max_seconds: float | None
    tuning_data_point: str
    notes: str | None = None


@dataclasses.dataclass(frozen=True)
class RulePrediction:
    rule_id: str
    family: Family
    title: str
    source_findings_indices: tuple[int, ...]
    clean: Prediction
    incremental: Prediction
    confidence: Confidence
    prerequisites: tuple[str, ...] = ()
    applies_when: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclasses.dataclass
class SimulationContext:
    """Inputs every predictor can read.

    The orchestrator constructs this once per simulate run.
    """

    diagnosis: dict[str, Any]
    measurement: dict[str, Any] | None
    project_path: pathlib.Path
    baseline_clean_seconds: float | None
    baseline_incremental_seconds: float | None

    def has_measurement(self) -> bool:
        return self.measurement is not None


def to_prediction_dict(prediction: Prediction) -> dict[str, Any]:
    """Serialise a Prediction for the simulation artifact."""

    return {
        "method": prediction.method,
        "estimate_seconds": prediction.estimate_seconds,
        "min_seconds": prediction.min_seconds,
        "max_seconds": prediction.max_seconds,
        "tuning_data_point": prediction.tuning_data_point,
        "notes": prediction.notes,
    }


def to_rule_prediction_dict(rule_prediction: RulePrediction) -> dict[str, Any]:
    """Serialise a RulePrediction for the simulation artifact."""

    return {
        "rule_id": rule_prediction.rule_id,
        "family": rule_prediction.family,
        "title": rule_prediction.title,
        "source_findings": {
            "indices": list(rule_prediction.source_findings_indices),
            "count": len(rule_prediction.source_findings_indices),
        },
        "clean": to_prediction_dict(rule_prediction.clean),
        "incremental": to_prediction_dict(rule_prediction.incremental),
        "confidence": rule_prediction.confidence,
        "prerequisites": list(rule_prediction.prerequisites),
        "applies_when": list(rule_prediction.applies_when),
        "notes": list(rule_prediction.notes),
    }


def _median_seconds_for(
    measurement: dict[str, Any] | None,
    build_type: str,
) -> float | None:
    """Pull median wall-clock for ``build_type`` from a Phase A measurement.json.

    Phase A schema: ``summary.<build_type>.median_seconds`` is the
    canonical location. ``runs.<build_type>`` is a list of per-repeat
    records (used by other consumers); the median lives in summary.
    Returns None if either layer is missing.
    """

    if not measurement:
        return None
    summary = measurement.get("summary") or {}
    bucket = summary.get(build_type) or {}
    median = bucket.get("median_seconds")
    if isinstance(median, (int, float)):
        return float(median)
    return None


def baseline_clean_seconds_from_measurement(
    measurement: dict[str, Any] | None,
) -> float | None:
    """Pull the median clean-build wall-clock from a Phase A measurement.json."""

    return _median_seconds_for(measurement, "clean")


def baseline_incremental_seconds_from_measurement(
    measurement: dict[str, Any] | None,
) -> float | None:
    """Pull the median incremental-build wall-clock from a Phase A measurement.json."""

    return _median_seconds_for(measurement, "incremental")
