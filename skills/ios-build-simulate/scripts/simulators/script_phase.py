"""Per-rule predictors for script-phase findings.

Rules covered:

- ``script-phase/random-sleep``           — F1
- ``script-phase/missing-debug-guard``    — F2
- ``script-phase/missing-output-declarations`` — F3
- ``script-phase/swiftlint-on-build``     — F8

Each predict_*() consumes the slice of diagnosis findings sharing its
rule_id and returns ONE RulePrediction (per-rule aggregation).
"""

from __future__ import annotations

import math
import re
from typing import Any

from . import (
    Confidence,
    Prediction,
    RulePrediction,
    SimulationContext,
)


_RANDOM_SLEEP_BOUND_PATTERN = re.compile(
    r"\$RANDOM\s*%\s*(?P<bound>\d+)",
    re.IGNORECASE,
)


def _indices(findings: list[tuple[int, dict[str, Any]]]) -> tuple[int, ...]:
    return tuple(idx for idx, _f in findings)


def predict_random_sleep(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: SimulationContext,
) -> RulePrediction:
    """F1 — random sleep in script phase. Per-build cost is the sleep duration.

    Aggregates across hits (rare to have >1). Mean = (N+1)/2 for `$RANDOM
    % N` (uniform 0..N-1, plus the literal `+ 1` offset, giving 1..N
    inclusive → mean (N+1)/2). When the bound can't be parsed from
    evidence.raw, default to 5s mean / 1s min / 10s max.
    """

    total_estimate = 0.0
    total_min = 0.0
    total_max = 0.0
    bounds_parsed: list[int] = []

    for _idx, finding in findings:
        evidence = finding.get("evidence") or {}
        raw = evidence.get("raw") or ""
        match = _RANDOM_SLEEP_BOUND_PATTERN.search(raw)
        if match:
            bound = int(match.group("bound"))
            bounds_parsed.append(bound)
            mean = (bound + 1) / 2.0
            total_estimate += mean
            total_min += 1.0
            total_max += float(bound)
        else:
            total_estimate += 5.0
            total_min += 1.0
            total_max += 10.0

    bounds_str = (
        f"$RANDOM%{','.join(str(b) for b in bounds_parsed)} parsed from evidence.raw"
        if bounds_parsed
        else "$RANDOM bound unparsed; defaulted to literal 1-10s"
    )
    tuning = (
        "(deferred to v1.1) — F1 calibrated heuristic; no `sleep "
        "$RANDOM` pattern observed in Wikipedia-iOS@9200297c15 or "
        "NetNewsWire@build-comparison-base script phases. Estimate "
        f"applied: mean 5.5s, range 1-10s ({len(findings)} finding(s) "
        f"aggregated; {bounds_str})"
    )

    estimate = -total_estimate
    pred = Prediction(
        method="heuristic",
        estimate_seconds=estimate,
        min_seconds=-total_max,
        max_seconds=-total_min,
        tuning_data_point=tuning,
        notes=(
            "Sleep runs unconditionally on every Debug, InHouse, and "
            "Distribution build; clean and incremental cost is the same."
        ),
    )

    confidence: Confidence = "high" if bounds_parsed else "medium"

    return RulePrediction(
        rule_id="script-phase/random-sleep",
        family="script-phase",
        title=f"Random sleep in {len(findings)} script phase(s)",
        source_findings_indices=_indices(findings),
        clean=pred,
        incremental=pred,
        confidence=confidence,
        prerequisites=(),
        applies_when=("Sleep is removed from the script body — surgical edit, no project-shape dependency",),
        notes=(
            "Predicted Δ is the sum across findings; remove the sleep line(s) to realise it.",
            "Lowest-risk fix in the effectiveness-gate menu (deletion of one literal line).",
        ),
    )


def predict_missing_debug_guard(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: SimulationContext,
) -> RulePrediction:
    """F2 — artifact-upload phase without CONFIGURATION early-exit guard.

    Per defaults.md: aggregated artifact-upload phases combine to ~3s
    on incremental Debug+sim builds. We aggregate at 1.5s per finding
    with a +/- 0.5s envelope.
    """

    n = len(findings)
    per_finding = 1.5
    estimate = -per_finding * n

    tuning = (
        "(deferred to v1.1) — F2 magnitude calibration; v1.0.0 ships F2 "
        "as informational manual recipe (per skills/ios-build-fix/SKILL.md). "
        "Neither Wikipedia-iOS@9200297c15 nor NetNewsWire@build-comparison-base "
        "ships a triggering artifact-upload phase. Estimate applied: ~1.5s "
        f"per finding x {n} finding(s) aggregated (one borderline path-only "
        "match expected on Crashlytics-Run Script -> firebase-ios-sdk/"
        "Crashlytics/run, treated as accept-as-is)"
    )

    pred = Prediction(
        method="literature",
        estimate_seconds=estimate,
        min_seconds=-2.0 * n,
        max_seconds=-1.0 * n,
        tuning_data_point=tuning,
        notes=(
            "Wrap each artifact-upload script body in "
            "[[ \"$CONFIGURATION\" == \"Debug\" ]] && exit 0."
        ),
    )

    return RulePrediction(
        rule_id="script-phase/missing-debug-guard",
        family="script-phase",
        title=f"Artifact-upload phase(s) missing CONFIGURATION guard ({n} hit(s))",
        source_findings_indices=_indices(findings),
        clean=pred,
        incremental=pred,
        confidence="medium",
        prerequisites=(),
        applies_when=(
            "Build configuration is Debug",
            "Each .sh body uses bash/sh shebang and supports the early-exit syntax",
        ),
        notes=(
            "Heuristic match list: firebase, crashlytics, upload, dsym, fullstory, datadog, sentry, bugsnag.",
            "Borderline path-component matches (e.g. SourcePackages/.../firebase-ios-sdk/Crashlytics/run) "
            "are real F2 cases — Firebase's run binary uploads dSYMs even on "
            "Debug simulator builds without a guard.",
        ),
    )


def predict_missing_output_declarations(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: SimulationContext,
) -> RulePrediction:
    """F3 — script phase declares no outputPaths.

    Aggregate per-phase delta (4s ±1; conservative upper bound observed
    on the development-time corpus, while Wikipedia-iOS@9200297c15
    PhaseScriptExecution averages 2.18s/phase clean — see
    references/defaults.md F3 row) but cap the sum at sqrt(N) * 4 to
    model the build system's ability to parallelise these phases when
    sandbox + fuse are also enabled. The cap prevents a 15-hit aggregate
    from claiming 60s of improvement when the realistic win after
    enabling output declarations + sandbox + fuse is ~10-15s incremental.
    """

    n = len(findings)
    per_phase = 4.0
    raw_sum = per_phase * n
    capped = math.sqrt(n) * per_phase if n > 1 else raw_sum
    estimate = -capped

    high_impact_count = sum(
        1
        for _idx, f in findings
        if (f.get("impact_category") == "high")
    )

    tuning = (
        f"measured-on-wikipedia-ios@9200297c15: "
        f"{n} phases declare no outputPaths "
        f"({high_impact_count} alwaysOutOfDate=False -> high impact). "
        f"Per-phase 4s ±1 (defaults.md script-phase/missing-output-declarations); "
        f"sum capped at sqrt({n}) * 4 = {capped:.1f}s to model parallel fan-out "
        "post-sandbox+fuse (raw N*4 = {raw} would over-claim). Wikipedia "
        "PhaseScriptExecution 6.54s/3 phases clean = ~2.18s/phase mean "
        "(wikipedia-ios-analysis.md:46) — 4s estimate is the conservative "
        "upper bound for SwiftLint-heavy phases.".format(raw=raw_sum)
    )

    pred = Prediction(
        method="measured-on-wikipedia-ios",
        estimate_seconds=estimate,
        min_seconds=-capped * 1.25,
        max_seconds=-capped * 0.5,
        tuning_data_point=tuning,
        notes=(
            "Declaring outputPaths lets Xcode mark the phase up-to-date "
            "when inputs are unchanged; downstream parallel work no longer "
            "blocks on it."
        ),
    )

    return RulePrediction(
        rule_id="script-phase/missing-output-declarations",
        family="script-phase",
        title=f"Script phase(s) without output declarations ({n} hit(s))",
        source_findings_indices=_indices(findings),
        clean=pred,
        incremental=pred,
        confidence="medium",
        prerequisites=(),
        applies_when=(
            "Each declared output is a stable path the phase actually writes",
            "Pairs with build-setting/script-sandboxing-disabled and "
            "build-setting/fuse-build-script-phases-disabled to realise the "
            "parallelism win",
        ),
        notes=(
            "Cap = sqrt(N) * per_phase models post-sandbox+fuse fan-out.",
            "alwaysOutOfDate=True findings still emit but at impact_category=medium.",
        ),
    )


def predict_swiftlint_on_build(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: SimulationContext,
) -> RulePrediction:
    """F8 — SwiftLint as a build phase.

    Heuristic: 2s mean (1-6 range) on incremental, 3s mean (1-6 range) on
    clean. Low confidence.
    """

    n = len(findings)
    estimate_inc = -2.0 * n
    estimate_cln = -3.0 * n

    tuning = (
        f"measured-on-wikipedia-ios@9200297c15: 3 SwiftLint phases on "
        f"Wikipedia/Staging/Experimental targets, PhaseScriptExecution "
        f"5.82s incremental = 39% of 14.93s wall-clock = 22x the "
        f"SwiftCompile of the touched file (0.26s) — "
        f"wikipedia-ios-analysis.md:55,76. Heuristic 1-6s per phase x "
        f"{n} hit(s); incremental mean 2s, clean mean 3s. Phase blocks "
        "the compile pipeline for its full duration; pre-commit hook + "
        "editor-on-save recovers the time without losing enforcement."
    )

    clean_pred = Prediction(
        method="heuristic",
        estimate_seconds=estimate_cln,
        min_seconds=-6.0 * n,
        max_seconds=-1.0 * n,
        tuning_data_point=tuning,
        notes="Clean-build estimate slightly higher than incremental — full lint pass on cold cache.",
    )
    incremental_pred = Prediction(
        method="heuristic",
        estimate_seconds=estimate_inc,
        min_seconds=-3.0 * n,
        max_seconds=-1.0 * n,
        tuning_data_point=tuning,
        notes="SwiftLint typically lints only changed files on incremental but the phase itself blocks regardless.",
    )

    return RulePrediction(
        rule_id="script-phase/swiftlint-on-build",
        family="script-phase",
        title=f"SwiftLint runs as build phase on {n} target(s)",
        source_findings_indices=_indices(findings),
        clean=clean_pred,
        incremental=incremental_pred,
        confidence="low",
        prerequisites=(),
        applies_when=(
            "SwiftLint is replaced with a pre-commit hook OR an editor-on-save lint, not removed entirely",
        ),
        notes=(
            "Low confidence — SwiftLint runtime varies with rule set and file count.",
            "Removing without replacement loses lint enforcement; the recommendation is "
            "to move enforcement, not drop it.",
        ),
    )
