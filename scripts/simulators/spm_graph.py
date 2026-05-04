"""Per-rule predictors for SPM-graph findings.

Rules covered:

- ``spm/swift-syntax-not-prebuilt`` — F6
- ``spm/oversized-module``          — F7
"""

from __future__ import annotations

from typing import Any

from . import (
    Prediction,
    RulePrediction,
    SimulationContext,
)


# Heuristic per-file emit cost on REDACTED (defaults.md): aggregate
# SwiftEmitModule across modules averages ~0.05-0.1s per file. Use the
# conservative end so a 794-file module is predicted ~40s clean impact
# (matches Phase A estimate 39.7s).
_PER_FILE_EMIT_SECONDS_CLEAN = 0.05
_PER_FILE_EMIT_SECONDS_INCREMENTAL = 0.04  # Per-file recompile when editing inside the module.


def _indices(findings: list[tuple[int, dict[str, Any]]]) -> tuple[int, ...]:
    return tuple(idx for idx, _f in findings)


def predict_swift_syntax_not_prebuilt(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: SimulationContext,
    f6_verified: bool = False,
) -> RulePrediction:
    """F6 — swift-syntax pin builds from source on clean.

    The Xcode 26 prebuilt-swift-syntax mechanism is documented at the
    Xcode 26 release notes URL but was UNVERIFIED at the line level
    until Phase A's S6a check. ``f6_verified`` is wired through the
    registry from the orchestrator's Phase A-deferred-follow-up result.
    """

    if f6_verified:
        method = "literature"
        confidence = "low"
        tuning = (
            "Xcode 26 release notes (line-level verified in Phase A S6a): "
            "prebuilt swift-syntax mechanism + opt-in setting documented. "
            "Predicted clean Δ heuristic 5-20s based on transitive root "
            "(REDACTED Package.resolved swift-syntax 510.0.3, transitive via "
            "third-party SDK using macros)."
        )
        clean_pred = Prediction(
            method=method,
            estimate_seconds=-12.0,
            min_seconds=-20.0,
            max_seconds=-5.0,
            tuning_data_point=tuning,
            notes="Wide range — depends on how many macro-using packages reach swift-syntax transitively.",
        )
        notes = (
            "Rule fires whenever any reachable Package.resolved pins swift-syntax.",
            "Phase A S6a confirmed the Xcode 26 prebuilt mechanism is documented.",
        )
    else:
        method = "literature"
        confidence = "low"
        tuning = (
            "REDACTED Package.resolved swift-syntax 510.0.3 (defaults.md). "
            "Xcode 26 prebuilt-swift-syntax claim UNVERIFIED at line level "
            "— see references/sources.md. Predicted Δ surfaced as best-effort "
            "heuristic; Phase A S6a must confirm before Phase A fix applies."
        )
        clean_pred = Prediction(
            method=method,
            estimate_seconds=-12.0,
            min_seconds=-20.0,
            max_seconds=-5.0,
            tuning_data_point=tuning,
            notes="UNVERIFIED — Xcode 26 prebuilt-swift-syntax setting not confirmed line-by-line.",
        )
        notes = (
            "Rule fires whenever any reachable Package.resolved pins swift-syntax.",
            "F6 citation flagged UNVERIFIED in references/sources.md until "
            "Phase A S6a confirms (or replaces with a better citation).",
        )

    incremental_pred = Prediction(
        method="literature",
        estimate_seconds=0.0,
        min_seconds=0.0,
        max_seconds=0.0,
        tuning_data_point=(
            "F6 is a clean-build finding — swift-syntax compiles once and "
            "the result is cached for subsequent incremental builds. No "
            "incremental wall-clock effect."
        ),
        notes=None,
    )

    return RulePrediction(
        rule_id="spm/swift-syntax-not-prebuilt",
        family="spm",
        title="swift-syntax builds from source on clean (no prebuilt opt-in)",
        source_findings_indices=_indices(findings),
        clean=clean_pred,
        incremental=incremental_pred,
        confidence=confidence,
        prerequisites=(),
        applies_when=(
            "Xcode 26 toolchain available",
            "The prebuilt-opt-in setting is documented and supported (per Phase A S6a verify)",
        ),
        notes=notes,
    )


def predict_oversized_module(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: SimulationContext,
) -> RulePrediction:
    """F7 — local Package.swift module with >= 200 .swift files.

    Per-finding clean impact = source_count * 0.05s (heuristic emit cost).
    Incremental impact applies only when the user edits inside the module
    — surfaced via the applies_when field, not the bare numeric.
    """

    total_clean = 0.0
    per_finding_breakdown: list[str] = []

    for _idx, finding in findings:
        evidence = finding.get("evidence") or {}
        # Evidence has the source count somewhere; Phase A spm_graph emits
        # filesystem-walk evidence. Pull source_count from the value field
        # (Phase A emit shape) or fall back to 200 (the threshold).
        value = (evidence.get("value") or "").strip()
        source_count = 200
        # Try to parse "source_count=N" or "N .swift files" etc.
        for token in value.replace(",", " ").split():
            if token.isdigit():
                source_count = int(token)
                break
        clean_impact = source_count * _PER_FILE_EMIT_SECONDS_CLEAN
        total_clean += clean_impact
        per_finding_breakdown.append(
            f"{evidence.get('path', '<unknown>')}={source_count} files (~{clean_impact:.1f}s clean)"
        )

    clean_estimate = -total_clean

    tuning = (
        "REDACTED REDACTED module file counts (defaults.md): REDACTED=794, "
        f"REDACTED=330. Per-file emit ~0.05s clean. Aggregated "
        f"across {len(findings)} finding(s): "
        f"{'; '.join(per_finding_breakdown) if per_finding_breakdown else '(no findings)'}."
    )

    clean_pred = Prediction(
        method="measured-on-REDACTED",
        estimate_seconds=clean_estimate,
        min_seconds=clean_estimate * 1.5,
        max_seconds=clean_estimate * 0.5,
        tuning_data_point=tuning,
        notes=(
            "Splitting the oversized module into smaller targets is the "
            "real fix — per-file emit cost in a 794-file module exceeds "
            "the parallel-emit budget on a typical macbook."
        ),
    )

    # Incremental: only applies when the user edits a file inside the module.
    incremental_pred = Prediction(
        method="heuristic",
        estimate_seconds=0.0,
        min_seconds=-2.0 * len(findings),
        max_seconds=0.0,
        tuning_data_point=(
            "Incremental impact is conditional on edit location: a one-line "
            "change inside a 794-file module re-emits the entire module, "
            "potentially adding 60-120s to incremental wall-clock vs the "
            f"~50s REDACTED touched-AppDelegate baseline. Surfaced as 0s default "
            "with the conditional captured in applies_when."
        ),
        notes="0s default — actual cost surfaces only when edit is inside the module.",
    )

    return RulePrediction(
        rule_id="spm/oversized-module",
        family="spm",
        title=f"Oversized local SPM module(s) ({len(findings)} hit(s))",
        source_findings_indices=_indices(findings),
        clean=clean_pred,
        incremental=incremental_pred,
        confidence="medium",
        prerequisites=(),
        applies_when=(
            "Edit inside an oversized module — incremental impact materialises only on file-level edits within the module",
            "Module is split into 2+ smaller targets along a natural seam (feature module, layer)",
        ),
        notes=(
            "F7 fix is multi-day refactor work; Phase A simulate flags it but "
            "Phase A fix does NOT auto-apply. Phase A effectiveness gate "
            "should NOT pick this rule.",
        ),
    )
