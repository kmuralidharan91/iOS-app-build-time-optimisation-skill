"""Per-rule predictor for asset-catalog findings.

Rules covered:

- ``asset-catalog/incremental-recompile`` — F5
"""

from __future__ import annotations

from typing import Any

from . import (
    Prediction,
    RulePrediction,
    SimulationContext,
)


def _indices(findings: list[tuple[int, dict[str, Any]]]) -> tuple[int, ...]:
    return tuple(idx for idx, _f in findings)


def _critical_path_node_seconds(measurement: dict[str, Any] | None) -> float | None:
    """Pull CompileAssetCatalogVariant duration from Phase A measurement.json.

    Tolerates both shapes the diagnose analyzer also accepts:
    - Phase A schema: ``{dominant_task, duration_seconds}``
    - hypothetical xcresult-target-graph future: ``{class_name, total_seconds}``
    """

    if not measurement:
        return None
    cp_root = measurement.get("critical_path") or {}
    incremental = cp_root.get("incremental") or {}
    nodes = incremental.get("nodes") or []
    for node in nodes:
        name = node.get("dominant_task") or node.get("class_name") or ""
        if name == "CompileAssetCatalogVariant":
            duration = node.get("duration_seconds") or node.get("total_seconds")
            if isinstance(duration, (int, float)):
                return float(duration)
    return None


def predict_incremental_recompile(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: SimulationContext,
) -> RulePrediction:
    """F5 — CompileAssetCatalogVariant runs every incremental build.

    Predicted incremental Δ = -<duration_seconds> from measurement.json.
    Clean Δ = 0 (asset catalog is part of the clean budget regardless;
    the F5 fix is about *incremental*-cache invalidation, not making
    actool faster).
    """

    measured = _critical_path_node_seconds(ctx.measurement)

    if measured is not None:
        incremental_estimate = -measured
        method = "measured-on-REDACTED"
        confidence = "high"
        tuning = (
            "Phase A measurement.json incremental.critical_path.nodes "
            f"CompileAssetCatalogVariant = {measured:.3f}s (REDACTED REDACTED, "
            "touched-AppDelegate.swift incremental). Predicted Δ is the "
            "literal node duration — fixing the upstream input (e.g. "
            "Step6_resetXCAssets RESET_RESOURCES guard) recovers it."
        )
        notes_text = (
            f"measurement.json supplied; node duration {measured:.3f}s used directly."
        )
    else:
        # Fall back to defaults.md REDACTED 4/26 baseline = 8.694s.
        incremental_estimate = -4.366
        method = "measured-on-REDACTED"
        confidence = "medium"
        tuning = (
            "No measurement.json supplied; defaulting to Phase A REDACTED REDACTED "
            "incremental measurement 4.366s (defaults.md "
            "asset-catalog/incremental-recompile)."
        )
        notes_text = (
            "Fallback to REDACTED reference data — supply --measurement-artifact "
            "for project-specific prediction."
        )

    incremental_pred = Prediction(
        method=method,
        estimate_seconds=incremental_estimate,
        min_seconds=-(abs(incremental_estimate) * 1.5),
        max_seconds=-1.0,
        tuning_data_point=tuning,
        notes=notes_text,
    )

    clean_pred = Prediction(
        method="measured-on-REDACTED",
        estimate_seconds=0.0,
        min_seconds=0.0,
        max_seconds=0.0,
        tuning_data_point=(
            "F5 is an incremental-only finding — clean builds always "
            "compile the asset catalog, so there is no clean-build savings "
            "from fixing the incremental invalidation cause."
        ),
        notes="Surface 0 explicitly so Phase A fix doesn't claim clean improvement on F5.",
    )

    return RulePrediction(
        rule_id="asset-catalog/incremental-recompile",
        family="asset-catalog",
        title="Asset catalog recompiles on every incremental build",
        source_findings_indices=_indices(findings),
        clean=clean_pred,
        incremental=incremental_pred,
        confidence=confidence,
        prerequisites=(),
        applies_when=(
            "An upstream input (likely a script phase that touches asset files) is "
            "modifying asset-catalog inputs on every build — locate and gate it",
            "On REDACTED specifically: Step6_resetXCAssets is the documented root cause "
            "(per optimization-plan.md Phase B Backtrace investigation)",
        ),
        notes=(
            "F5 fix is about identifying the upstream invalidator, not making "
            "actool faster. The wall-clock recovery equals the node duration "
            "in the Phase A measurement.",
        ),
    )
