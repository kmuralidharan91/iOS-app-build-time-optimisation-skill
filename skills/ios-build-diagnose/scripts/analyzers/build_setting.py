"""Build-setting analyzer — F4, F9 + PR-#2 sandboxing/fuse audit.

Reads ``DiagnosisContext.resolved_settings`` (the merged
``xcodebuild -showBuildSettings -json`` output) and applies the PR-#1
effective-settings state table:

- explicit pbxproj value -> audit literally (we just check the
  resolved value);
- unset + xcodebuild reports the recommended default -> pass (no
  finding);
- unset + xcodebuild reports a different default -> fail with the
  resolved value as evidence;
- xcodebuild silent (key missing from the dict) -> fall back to
  ``(unset)`` evidence.

When ``resolved_settings`` is empty (xcodebuild missing / network down /
non-JSON output), every build-setting rule short-circuits with a note
explaining the limitation; the orchestrator surfaces that note in the
artifact's top-level ``notes`` array.

Findings (counted toward F1–F9 recall):
- ``build-setting/compilation-cache-disabled``  — F4
- ``build-setting/eager-linking-disabled``       — F9 (impact=low)

Additional recommendations (PR-#2; counted separately):
- ``build-setting/script-sandboxing-disabled``       — ENABLE_USER_SCRIPT_SANDBOXING
- ``build-setting/fuse-build-script-phases-disabled`` — FUSE_BUILD_SCRIPT_PHASES
"""

from __future__ import annotations

from . import (
    Citation,
    DiagnosisContext,
    Evidence,
    Finding,
    Recommendation,
    WallClockPrediction,
)


_APPLE_BUILD_SETTINGS_REFERENCE_URL = (
    "https://developer.apple.com/documentation/xcode/build-settings-reference"
)
_APPLE_PARALLELIZATION_URL = "https://developer.apple.com/videos/play/wwdc2022/110364/"


def run(
    context: DiagnosisContext,
) -> tuple[list[Finding], list[Recommendation]]:
    findings: list[Finding] = []
    recommendations: list[Recommendation] = []

    if not context.has_resolved_settings():
        # Caller already adds a top-level note; no per-rule output.
        return findings, recommendations

    resolved = context.resolved_settings

    findings.extend(_check_compilation_cache(resolved, context.configuration))
    findings.extend(_check_eager_linking(resolved, context.configuration))
    recommendations.extend(_check_script_sandboxing(resolved, context.configuration))
    recommendations.extend(_check_fuse_build_script_phases(resolved, context.configuration))

    return findings, recommendations


def _evidence_value(resolved: dict[str, str], key: str) -> str:
    """Format the evidence value for a build-setting check.

    Mirrors the PR-#1 state table: when the key is present, return
    ``unset; resolved to <value>``-style markers when xcodebuild
    surfaces it as the default; when the key is absent, return
    ``(unset)``.
    """

    if key not in resolved:
        return "(unset)"
    return resolved[key]


def _check_compilation_cache(
    resolved: dict[str, str],
    configuration: str,
) -> list[Finding]:
    key = "COMPILATION_CACHE_ENABLE_CACHING"
    value = resolved.get(key, "")
    if value == "YES":
        return []
    return [
        Finding(
            rule_id="build-setting/compilation-cache-disabled",
            family="build-setting",
            title=f"{key} is not enabled (resolved value: {_evidence_value(resolved, key)})",
            evidence=Evidence(
                kind="setting",
                key=key,
                value=_evidence_value(resolved, key),
                configuration=configuration,
            ),
            impact_category="high",
            wall_clock_predicted=WallClockPrediction(
                method="measured-on-REDACTED",
                estimate_seconds=125.0,
                min_seconds=60.0,
                max_seconds=180.0,
                notes=(
                    "On REDACTED Phase D measurement: enabling this setting cut "
                    "warm-cache clean-build wall-clock by ~45.6%; the "
                    "estimate is the absolute seconds saved on a 275s "
                    "clean baseline. Trade-off: ~10s incremental cost on "
                    "touched-file change because the cache invalidates "
                    "more files than Xcode's incremental tracker."
                ),
            ),
            citation=Citation(
                url=_APPLE_BUILD_SETTINGS_REFERENCE_URL,
                source="Apple Build Settings Reference — COMPILATION_CACHE_ENABLE_CACHING",
            ),
            source_method="showBuildSettings",
            notes=(),
        )
    ]


def _check_eager_linking(
    resolved: dict[str, str],
    configuration: str,
) -> list[Finding]:
    key = "EAGER_LINKING"
    value = resolved.get(key, "")
    if value == "YES":
        return []
    return [
        Finding(
            rule_id="build-setting/eager-linking-disabled",
            family="build-setting",
            title=f"{key} is not enabled (resolved value: {_evidence_value(resolved, key)})",
            evidence=Evidence(
                kind="setting",
                key=key,
                value=_evidence_value(resolved, key),
                configuration=configuration,
            ),
            impact_category="low",
            wall_clock_predicted=WallClockPrediction(
                method="measured-on-REDACTED",
                estimate_seconds=0.0,
                min_seconds=0.0,
                max_seconds=8.0,
                notes=(
                    "Predicted improvement is project-shaped; on REDACTED the "
                    "Phase v1->v2 measurement showed zero improvement and "
                    "the change was reverted. Surface as low-confidence; "
                    "rely on simulate -> fix -> re-measure to refuse "
                    "claims of improvement when the actual delta is null."
                ),
            ),
            citation=Citation(
                url=_APPLE_BUILD_SETTINGS_REFERENCE_URL,
                source="Apple Build Settings Reference — EAGER_LINKING",
            ),
            source_method="showBuildSettings",
            notes=(
                "Low-confidence finding by design: project-shape sensitive.",
            ),
        )
    ]


def _check_script_sandboxing(
    resolved: dict[str, str],
    configuration: str,
) -> list[Recommendation]:
    key = "ENABLE_USER_SCRIPT_SANDBOXING"
    value = resolved.get(key, "")
    if value == "YES":
        return []
    return [
        Recommendation(
            rule_id="build-setting/script-sandboxing-disabled",
            family="build-setting",
            title=(
                f"{key} is not enabled (resolved value: "
                f"{_evidence_value(resolved, key)})"
            ),
            evidence=Evidence(
                kind="setting",
                key=key,
                value=_evidence_value(resolved, key),
                configuration=configuration,
            ),
            impact_category="medium",
            wall_clock_predicted=WallClockPrediction(
                method="literature",
                estimate_seconds=None,
                min_seconds=None,
                max_seconds=None,
                notes=(
                    "Indirect wall-clock impact: enabling sandboxing forces "
                    "phases to declare every input/output, which then lets "
                    "the build system parallelise them and skip them when "
                    "inputs are unchanged. See WWDC22 110364."
                ),
            ),
            citation=Citation(
                url=_APPLE_PARALLELIZATION_URL,
                source="WWDC22 110364 — Demystify parallelization in Xcode builds",
            ),
            source_method="showBuildSettings",
            notes=(
                "Suite value-add (PR-#2 audit); not part of the F1-F9 ground truth.",
            ),
        )
    ]


def _check_fuse_build_script_phases(
    resolved: dict[str, str],
    configuration: str,
) -> list[Recommendation]:
    key = "FUSE_BUILD_SCRIPT_PHASES"
    value = resolved.get(key, "")
    if value == "YES":
        return []
    return [
        Recommendation(
            rule_id="build-setting/fuse-build-script-phases-disabled",
            family="build-setting",
            title=(
                f"{key} is not enabled (resolved value: "
                f"{_evidence_value(resolved, key)})"
            ),
            evidence=Evidence(
                kind="setting",
                key=key,
                value=_evidence_value(resolved, key),
                configuration=configuration,
            ),
            impact_category="medium",
            wall_clock_predicted=WallClockPrediction(
                method="literature",
                estimate_seconds=None,
                min_seconds=None,
                max_seconds=None,
                notes=(
                    "Wall-clock impact scales with phase count and "
                    "spawn overhead per phase. REDACTED has 14 "
                    "PBXShellScriptBuildPhase entries; fusing them "
                    "amortises shell startup across the chain."
                ),
            ),
            citation=Citation(
                url=_APPLE_PARALLELIZATION_URL,
                source="WWDC22 110364 — Demystify parallelization in Xcode builds",
            ),
            source_method="showBuildSettings",
            notes=(
                "Suite value-add (PR-#2 audit); not part of the F1-F9 ground truth.",
                "Requires sandboxing to be enabled first (sandbox declares the "
                "input/output graph that fuse depends on).",
            ),
        )
    ]
