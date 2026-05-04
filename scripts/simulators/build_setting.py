"""Per-rule predictors for build-setting findings + PR-#2 recommendations.

Rules covered:

- ``build-setting/compilation-cache-disabled``         — F4 (finding)
- ``build-setting/eager-linking-disabled``             — F9 (finding)
- ``build-setting/script-sandboxing-disabled``         — PR-#2 recommendation
- ``build-setting/fuse-build-script-phases-disabled``  — PR-#2 recommendation
"""

from __future__ import annotations

from typing import Any

from . import (
    Prediction,
    RulePrediction,
    SimulationContext,
)


_NSK_REFERENCE_CLEAN_SECONDS = 275.0  # 4/26 baseline (defaults.md)
_NSK_PHASE_D_REDUCTION = 0.456


def _indices(findings: list[tuple[int, dict[str, Any]]]) -> tuple[int, ...]:
    return tuple(idx for idx, _f in findings)


def predict_compilation_cache_disabled(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: SimulationContext,
) -> RulePrediction:
    """F4 — COMPILATION_CACHE_ENABLE_CACHING is unset / NO.

    clean Δ = -0.456 * baseline_clean (REDACTED Phase D measurement); when no
    measurement.json baseline is available, fall back to the REDACTED 4/26
    reference 275s -> -125s.

    incremental Δ = +10s (regression cost; REDACTED Phase D measured this).
    """

    baseline = ctx.baseline_clean_seconds or _NSK_REFERENCE_CLEAN_SECONDS
    clean_estimate = -_NSK_PHASE_D_REDUCTION * baseline
    using_measurement = ctx.baseline_clean_seconds is not None

    method = "measured-on-REDACTED"
    confidence = "high" if using_measurement else "medium"

    clean_pred = Prediction(
        method=method,
        estimate_seconds=clean_estimate,
        min_seconds=-baseline * 0.55,
        max_seconds=-baseline * 0.30,
        tuning_data_point=(
            "REDACTED Phase D measurement: warm-cache clean Debug+sim build "
            "came in 45.6% faster than cold-cache equivalent (~125s saved "
            f"on a 275s baseline). Applied here against baseline = {baseline:.1f}s "
            f"({'measurement.json clean median' if using_measurement else 'REDACTED 4/26 reference fallback'})."
        ),
        notes=(
            "Warm cache only — first build after enabling populates the "
            "cache; only second-and-after builds reflect the speedup."
        ),
    )
    incremental_pred = Prediction(
        method="measured-on-REDACTED",
        estimate_seconds=10.0,
        min_seconds=5.0,
        max_seconds=15.0,
        tuning_data_point=(
            "REDACTED Phase D incremental: ~10s extra on touched-file change "
            "because cache invalidation cone is wider than Xcode's "
            "incremental tracker."
        ),
        notes="Positive value = regression. Net positive on every REDACTED measurement so far.",
    )

    return RulePrediction(
        rule_id="build-setting/compilation-cache-disabled",
        family="build-setting",
        title="COMPILATION_CACHE_ENABLE_CACHING is not enabled",
        source_findings_indices=_indices(findings),
        clean=clean_pred,
        incremental=incremental_pred,
        confidence=confidence,
        prerequisites=(),
        applies_when=(
            "Warm cache; first build after enabling primes the cache (~5-8 min on REDACTED)",
            "Project tolerates the ~10s incremental regression",
        ),
        notes=(
            "Trade-off explicitly surfaced — incremental cost is real and can be "
            "the deciding factor for projects whose dev loop is incremental-dominated.",
            "Net wall-clock impact (clean+incremental) is project-dependent; "
            "Phase A fix re-measures both before declaring success.",
        ),
    )


def predict_eager_linking_disabled(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: SimulationContext,
) -> RulePrediction:
    """F9 — EAGER_LINKING is unset / NO.

    REDACTED Phase v1->v2 measured zero clean improvement and the change was
    reverted. Predict 0s ±8 with low confidence; Phase A fix re-measure
    must refuse on null delta.
    """

    pred = Prediction(
        method="measured-on-REDACTED",
        estimate_seconds=0.0,
        min_seconds=-8.0,
        max_seconds=0.0,
        tuning_data_point=(
            "REDACTED Phase v1->v2 measurement: zero clean-build improvement; "
            "change was reverted in optimization-plan.md. Per defaults.md, "
            "F9 surfaces as low-confidence; simulate predicts 0s and Phase A "
            "fix re-measure refuses on null delta."
        ),
        notes=(
            "Project-shape sensitive — eager linking only helps projects "
            "whose linker waits dominate the critical path. Refusal-on-null "
            "is the design that catches this case automatically."
        ),
    )
    no_op = Prediction(
        method="measured-on-REDACTED",
        estimate_seconds=0.0,
        min_seconds=0.0,
        max_seconds=0.0,
        tuning_data_point=(
            "EAGER_LINKING affects scheduling of Ld tasks; no incremental "
            "build-time effect on touched-file change."
        ),
        notes=None,
    )

    return RulePrediction(
        rule_id="build-setting/eager-linking-disabled",
        family="build-setting",
        title="EAGER_LINKING is not enabled (low-confidence finding)",
        source_findings_indices=_indices(findings),
        clean=pred,
        incremental=no_op,
        confidence="low",
        prerequisites=(),
        applies_when=(
            "Project's link tasks dominate the critical path (pure-Swift "
            "dynamic frameworks linked by their dependents)",
        ),
        notes=(
            "Surface but prepare for null-delta. Phase A fix re-measure refuses "
            "to claim success when the actual improvement is null/regressive — "
            "test the refusal path here.",
        ),
    )


def predict_script_sandboxing_disabled(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: SimulationContext,
) -> RulePrediction:
    """PR-#2 — ENABLE_USER_SCRIPT_SANDBOXING is unset / NO.

    Indirect impact: sandboxing itself does not cut wall-clock; the win
    materialises via FUSE_BUILD_SCRIPT_PHASES once enabled. Predict
    estimate=None; the user sees this as a prerequisite recommendation,
    not a direct savings claim.
    """

    pred = Prediction(
        method="literature",
        estimate_seconds=None,
        min_seconds=None,
        max_seconds=None,
        tuning_data_point=(
            "WWDC22 110364 (Demystify parallelization in Xcode builds): "
            "sandboxing forces phase-input/output declarations, which is "
            "the precondition for FUSE_BUILD_SCRIPT_PHASES. Wall-clock "
            "win materialises through fuse, not sandbox itself."
        ),
        notes=(
            "Predicted as no-direct-Δ — apply this first, then enable "
            "fuse to capture the wall-clock benefit."
        ),
    )

    return RulePrediction(
        rule_id="build-setting/script-sandboxing-disabled",
        family="build-setting",
        title="ENABLE_USER_SCRIPT_SANDBOXING is not enabled (PR-#2 audit)",
        source_findings_indices=_indices(findings),
        clean=pred,
        incremental=pred,
        confidence="low",
        prerequisites=(
            "script-phase/missing-output-declarations",
        ),
        applies_when=(
            "Existing phases already declare every input/output (otherwise sandbox fails the build)",
        ),
        notes=(
            "Suite value-add (PR-#2 audit); not part of the F1-F9 ground truth.",
            "Apply target-by-target; Phase A fix carries the per-finding refusal-when-broken guarantee.",
        ),
    )


def predict_fuse_build_script_phases_disabled(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: SimulationContext,
) -> RulePrediction:
    """PR-#2 — FUSE_BUILD_SCRIPT_PHASES is unset / NO.

    Heuristic: ~0.5s saved per script phase (shell-startup amortisation)
    once preconditions are met. REDACTED has 14 script phases on develop ->
    ~7s clean / ~5s incremental savings.
    """

    n_phases = 14  # REDACTED REDACTED step-22 CSV count; future projects re-tune.
    clean_estimate = -0.5 * n_phases
    incremental_estimate = -0.4 * n_phases

    tuning = (
        f"WWDC22 110364: fusing {n_phases} script phases (REDACTED REDACTED "
        "step-22 CSV) amortises shell-startup overhead across the chain. "
        "Heuristic 0.5s clean / 0.4s incremental per phase; project-shape "
        "sensitive."
    )

    clean_pred = Prediction(
        method="heuristic",
        estimate_seconds=clean_estimate,
        min_seconds=-1.0 * n_phases,
        max_seconds=-0.2 * n_phases,
        tuning_data_point=tuning,
        notes="Wall-clock win scales with phase count and spawn overhead per phase.",
    )
    incremental_pred = Prediction(
        method="heuristic",
        estimate_seconds=incremental_estimate,
        min_seconds=-0.8 * n_phases,
        max_seconds=-0.1 * n_phases,
        tuning_data_point=tuning,
        notes=None,
    )

    return RulePrediction(
        rule_id="build-setting/fuse-build-script-phases-disabled",
        family="build-setting",
        title="FUSE_BUILD_SCRIPT_PHASES is not enabled (PR-#2 audit)",
        source_findings_indices=_indices(findings),
        clean=clean_pred,
        incremental=incremental_pred,
        confidence="low",
        prerequisites=(
            "build-setting/script-sandboxing-disabled",
            "script-phase/missing-output-declarations",
        ),
        applies_when=(
            "Sandbox is enabled (declares the input/output graph fuse depends on)",
            "Every phase declares its inputs and outputs",
        ),
        notes=(
            "Suite value-add (PR-#2 audit); not part of the F1-F9 ground truth.",
            "Apply ONLY after sandboxing is on and outputs are declared — "
            "WWDC22 110364 warning: 'an incomplete list of the inputs or outputs "
            "of a script phase can lead to data races which are very hard to debug'.",
        ),
    )
