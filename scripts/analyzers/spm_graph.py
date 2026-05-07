"""SPM-graph analyzer — F6, F7 + R1 suppression check.

Reads ``DiagnosisContext.package_graph`` (a ``PackageGraph`` from
``xcode_adapter.package_graph``) and applies three rules:

- ``spm/swift-syntax-not-prebuilt`` (F6) — fires when ``swift-syntax``
  is pinned in any reachable Package.resolved. Xcode 26+ ships a
  prebuilt swift-syntax that macro-using projects can opt into.
- ``spm/oversized-module`` (F7) — fires when a local Package.swift
  module under the project tree has ≥ 200 .swift files (default; see
  references/defaults.md). v1.0.0 validation against public corpora:
  Wikipedia-iOS@9200297c15 WMFComponents = 213 files (positive control;
  rule fires) vs WMFData = 103 (does not); NetNewsWire@build-comparison-base
  largest = Account 111 files (negative control; 14 packages, none over
  threshold). See docs/wikipedia-ios-analysis.md:90 and
  docs/netnewswire-analysis.md:91-101.
- ``spm/branch-pinned`` (R1 suppression) — only fires when a pin has
  a ``branch`` set AND no ``version``. The rule should NOT surface
  against well-maintained projects; when it does, the verification
  log records it as a false positive.
"""

from __future__ import annotations

from . import (
    Citation,
    DiagnosisContext,
    Evidence,
    Finding,
    WallClockPrediction,
)


_OVERSIZED_MODULE_THRESHOLD = 200
_XCODE26_RELEASE_NOTES_URL = (
    "https://developer.apple.com/documentation/xcode-release-notes/xcode-26-release-notes"
)
_APPLE_SWIFT_PACKAGE_URL = (
    "https://developer.apple.com/documentation/xcode/swift-packages"
)


def run(context: DiagnosisContext) -> list[Finding]:
    graph = context.package_graph
    if graph is None:
        return []

    findings: list[Finding] = []
    findings.extend(_check_swift_syntax(graph))
    findings.extend(_check_oversized_modules(graph))
    findings.extend(_check_branch_pinned(graph))
    return findings


def _check_swift_syntax(graph) -> list[Finding]:
    pin = next(
        (p for p in graph.pins if (p.name or "").lower() == "swift-syntax"),
        None,
    )
    if pin is None:
        return []
    return [
        Finding(
            rule_id="spm/swift-syntax-not-prebuilt",
            family="spm",
            title=(
                f"swift-syntax @ {pin.version or pin.revision or '<unknown>'} is "
                "compiled from source on clean builds"
            ),
            evidence=Evidence(
                kind="file_line",
                path=pin.source_resolved_path,
                value=(
                    f"name={pin.name} version={pin.version} "
                    f"revision={pin.revision} location={pin.location}"
                ),
            ),
            impact_category="medium",
            wall_clock_predicted=WallClockPrediction(
                method="heuristic",
                estimate_seconds=12.0,
                min_seconds=5.0,
                max_seconds=20.0,
                notes=(
                    "swift-syntax compiles for every supported architecture "
                    "and is one of the top contributors to clean-build cost "
                    "in projects that use Swift macros transitively. The "
                    "exact magnitude depends on the project size and the "
                    "transitive importer; simulate refines this against "
                    "Wikipedia-iOS / NetNewsWire."
                ),
            ),
            citation=Citation(
                url=_XCODE26_RELEASE_NOTES_URL,
                source="Xcode 26 release notes — prebuilt swift-syntax for macros",
            ),
            source_method="package-resolved",
            notes=(
                "Xcode 26+ supports a prebuilt swift-syntax distribution; "
                "opt in via the Xcode Package Dependencies UI or a project-"
                "level setting. Confirm the importer by walking transitive "
                "deps; common importers include macro-using SDKs.",
            ),
        )
    ]


def _check_oversized_modules(graph) -> list[Finding]:
    findings: list[Finding] = []
    for module in graph.local_modules:
        if module.source_count < _OVERSIZED_MODULE_THRESHOLD:
            continue
        impact = "medium" if module.source_count < 600 else "high"
        findings.append(
            Finding(
                rule_id="spm/oversized-module",
                family="spm",
                title=(
                    f"Local SPM module '{module.name}' has "
                    f"{module.source_count} Swift files"
                ),
                evidence=Evidence(
                    kind="filesystem",
                    path=module.path,
                    value=f"source_count={module.source_count}",
                ),
                impact_category=impact,
                wall_clock_predicted=WallClockPrediction(
                    method="measured-on-wikipedia-ios",
                    estimate_seconds=float(module.source_count) * 0.05,
                    min_seconds=10.0,
                    max_seconds=120.0,
                    notes=(
                        "Coarse heuristic: oversized modules force more "
                        "files to recompile per touched file. Real impact "
                        "depends on the call graph; simulate tunes the "
                        "per-file factor against measured incremental "
                        "spans inside the module. v1.0.0 positive control: "
                        "Wikipedia-iOS WMFComponents=213 files. "
                        "Per-module incremental-edit cost calibration "
                        "deferred to v1.x once a comparable oversized "
                        "module's incremental edit is benchmarked."
                    ),
                ),
                citation=Citation(
                    url=_APPLE_SWIFT_PACKAGE_URL,
                    source="Apple — Swift Packages overview (modularisation)",
                ),
                source_method="filesystem-walk",
                notes=(
                    f"Threshold: source_count >= {_OVERSIZED_MODULE_THRESHOLD} "
                    "(see references/defaults.md).",
                ),
            )
        )
    return findings


def _check_branch_pinned(graph) -> list[Finding]:
    findings: list[Finding] = []
    for pin in graph.pins:
        if not pin.branch or pin.version:
            continue
        findings.append(
            Finding(
                rule_id="spm/branch-pinned",
                family="spm",
                title=(
                    f"Package '{pin.name}' is branch-pinned "
                    f"(branch={pin.branch})"
                ),
                evidence=Evidence(
                    kind="file_line",
                    path=pin.source_resolved_path,
                    value=(
                        f"name={pin.name} branch={pin.branch} "
                        f"revision={pin.revision} location={pin.location}"
                    ),
                ),
                impact_category="medium",
                wall_clock_predicted=WallClockPrediction(
                    method="heuristic",
                    estimate_seconds=15.0,
                    min_seconds=5.0,
                    max_seconds=60.0,
                    notes=(
                        "Branch-pinned dependencies bypass SPM's resolution "
                        "cache; CI and clean-checkout builds re-fetch on "
                        "every run. Pin to a tagged version to recover the "
                        "fetch + resolve cost."
                    ),
                ),
                citation=Citation(
                    url=_APPLE_SWIFT_PACKAGE_URL,
                    source="Apple — Swift Packages: dependency rules",
                ),
                source_method="package-resolved",
                notes=(),
            )
        )
    return findings
