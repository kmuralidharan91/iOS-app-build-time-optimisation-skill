"""Rule_id -> predictor function dispatch.

The orchestrator in ``scripts/simulate.py`` iterates this mapping, slices
``diagnosis.findings[]`` by rule_id, and calls each predictor with the
slice + the SimulationContext.

A finding whose rule_id has no entry here gets a synthesised
``RulePrediction`` from ``predict_unknown`` so the artifact stays
complete (and surfaces the gap to the user).
"""

from __future__ import annotations

from typing import Any, Callable

from . import (
    Prediction,
    RulePrediction,
    SimulationContext,
)
from . import asset_catalog, build_setting, script_phase, spm_graph


# Predictor signature: takes (findings, ctx) and returns a RulePrediction.
# F6 has an extra ``f6_verified`` flag wired through a closure.
PredictorFn = Callable[
    [list[tuple[int, dict[str, Any]]], SimulationContext],
    RulePrediction,
]


def build_registry(*, f6_verified: bool = False) -> dict[str, PredictorFn]:
    """Construct the rule_id -> predictor mapping.

    ``f6_verified`` reflects the Phase A S6a follow-up outcome; the
    swift-syntax-not-prebuilt predictor varies its tuning_data_point
    text based on whether the Xcode 26 prebuilt mechanism was confirmed.
    """

    def f6_predictor(
        findings: list[tuple[int, dict[str, Any]]],
        ctx: SimulationContext,
    ) -> RulePrediction:
        return spm_graph.predict_swift_syntax_not_prebuilt(
            findings, ctx, f6_verified=f6_verified
        )

    return {
        "script-phase/random-sleep": script_phase.predict_random_sleep,
        "script-phase/missing-debug-guard": script_phase.predict_missing_debug_guard,
        "script-phase/missing-output-declarations": script_phase.predict_missing_output_declarations,
        "script-phase/swiftlint-on-build": script_phase.predict_swiftlint_on_build,
        "build-setting/compilation-cache-disabled": build_setting.predict_compilation_cache_disabled,
        "build-setting/eager-linking-disabled": build_setting.predict_eager_linking_disabled,
        "build-setting/script-sandboxing-disabled": build_setting.predict_script_sandboxing_disabled,
        "build-setting/fuse-build-script-phases-disabled": build_setting.predict_fuse_build_script_phases_disabled,
        "asset-catalog/incremental-recompile": asset_catalog.predict_incremental_recompile,
        "spm/swift-syntax-not-prebuilt": f6_predictor,
        "spm/oversized-module": spm_graph.predict_oversized_module,
    }


def predict_unknown(
    rule_id: str,
    findings: list[tuple[int, dict[str, Any]]],
    ctx: SimulationContext,
) -> RulePrediction:
    """Fallback for rule_ids without a registered predictor.

    Surfaces the gap explicitly so the artifact stays complete and the
    user knows a predictor is missing rather than getting silently
    dropped findings.
    """

    family_value = "script-phase"
    if findings:
        family_value = findings[0][1].get("family", "script-phase")
    if family_value not in ("script-phase", "build-setting", "asset-catalog", "spm"):
        family_value = "script-phase"

    indices = tuple(idx for idx, _f in findings)
    no_estimate = Prediction(
        method="heuristic",
        estimate_seconds=None,
        min_seconds=None,
        max_seconds=None,
        tuning_data_point=(
            f"No predictor registered for rule_id '{rule_id}'. Add one "
            "under scripts/simulators/ and register in registry.py."
        ),
        notes="Predictor gap; this prediction is a placeholder.",
    )
    return RulePrediction(
        rule_id=rule_id,
        family=family_value,
        title=f"(no predictor registered for {rule_id})",
        source_findings_indices=indices,
        clean=no_estimate,
        incremental=no_estimate,
        confidence="low",
        prerequisites=(),
        applies_when=(),
        notes=(
            "This rule fired in diagnosis but has no Phase A simulate "
            "predictor. Add scripts/simulators/<family>.py::predict_<rule>() "
            "and register it in registry.build_registry().",
        ),
    )
