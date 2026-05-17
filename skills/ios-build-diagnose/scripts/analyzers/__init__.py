"""Analyzer registry + Finding dataclass for ios-build-diagnose.

Each analyzer module under this package exposes a single ``run(context)``
entry point. The orchestrator in ``scripts/diagnose.py`` constructs a
``DiagnosisContext`` (project path, scheme, configuration, the loaded
measurement artifact, plus the adapter outputs the analyzers consume),
imports each analyzer, and concatenates the returned Finding lists.

The split between Finding and Recommendation is intentional:

- ``Finding`` lives in ``findings[]`` of the diagnosis artifact and is
  scored against the ground truth.
- ``Recommendation`` lives in ``additional_recommendations[]`` and
  carries suite-value-add (e.g. PR-#2 sandboxing + fuse audit) that is
  not in the F1–F9 ground truth. Keeping them separate lets the
  effectiveness gate stay 1:1 with the ground-truth file.
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any, Literal


ImpactCategory = Literal["high", "medium", "low", "unknown"]
EvidenceKind = Literal["file_line", "setting", "measurement", "filesystem"]
PredictionMethod = Literal[
    "measured-on-wikipedia-ios",
    "measured-on-netnewswire",
    "measurement-derived",
    "heuristic",
    "literature",
]
SourceMethod = Literal[
    "pbxproj-parse",
    "showBuildSettings",
    "package-resolved",
    "filesystem-walk",
    "timing-summary",
]


@dataclasses.dataclass(frozen=True)
class Evidence:
    kind: EvidenceKind
    path: str | None = None
    line: int | None = None
    key: str | None = None
    value: str | None = None
    configuration: str | None = None
    raw: str | None = None


@dataclasses.dataclass(frozen=True)
class WallClockPrediction:
    method: PredictionMethod
    estimate_seconds: float | None = None
    min_seconds: float | None = None
    max_seconds: float | None = None
    notes: str | None = None


@dataclasses.dataclass(frozen=True)
class Citation:
    url: str
    source: str


@dataclasses.dataclass(frozen=True)
class Finding:
    rule_id: str
    family: str
    title: str
    evidence: Evidence
    impact_category: ImpactCategory
    wall_clock_predicted: WallClockPrediction
    citation: Citation
    source_method: SourceMethod
    notes: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Recommendation:
    """Same shape as Finding; lives in additional_recommendations[]."""

    rule_id: str
    family: str
    title: str
    evidence: Evidence
    impact_category: ImpactCategory
    wall_clock_predicted: WallClockPrediction
    citation: Citation
    source_method: SourceMethod
    notes: tuple[str, ...] = ()


@dataclasses.dataclass
class DiagnosisContext:
    """Inputs every analyzer can read.

    The orchestrator constructs this once per diagnose run and passes
    the same instance to every analyzer.
    """

    project_path: pathlib.Path
    scheme: str | None
    configuration: str
    platform: str
    measurement: dict[str, Any] | None  # parsed measurement.json (benchmark artifact)
    resolved_settings: dict[str, str]
    script_phases: list[Any]   # list[ScriptPhase] from {xcode,bazel,tuist}_adapter
    package_graph: Any | None  # PackageGraph from {xcode,bazel,tuist}_adapter
    # Detected build system: "xcode" | "bazel" | "tuist". Analyzers use
    # this to suppress rules that key off Xcode-only settings when the
    # project is a Bazel build (e.g. F4 COMPILATION_CACHE_ENABLE_CACHING
    # has no Bazel analogue). Default "xcode" preserves the pre-v1.3
    # behaviour for any analyzer that doesn't explicitly opt in.
    build_system: str = "xcode"

    def has_resolved_settings(self) -> bool:
        return bool(self.resolved_settings)


def to_evidence_dict(evidence: Evidence) -> dict[str, Any]:
    """Convert an Evidence dataclass to a JSON-friendly dict (drops None values)."""

    out: dict[str, Any] = {"kind": evidence.kind}
    for field_name in ("path", "line", "key", "value", "configuration", "raw"):
        value = getattr(evidence, field_name)
        if value is not None:
            out[field_name] = value
    return out


def to_prediction_dict(prediction: WallClockPrediction) -> dict[str, Any]:
    """Convert a WallClockPrediction dataclass to a JSON-friendly dict."""

    return {
        "method": prediction.method,
        "estimate_seconds": prediction.estimate_seconds,
        "min_seconds": prediction.min_seconds,
        "max_seconds": prediction.max_seconds,
        "notes": prediction.notes,
    }


def to_finding_dict(finding: Finding | Recommendation) -> dict[str, Any]:
    """Serialise a Finding / Recommendation for the diagnosis artifact."""

    return {
        "rule_id": finding.rule_id,
        "family": finding.family,
        "title": finding.title,
        "evidence": to_evidence_dict(finding.evidence),
        "impact_category": finding.impact_category,
        "wall_clock_predicted_seconds": to_prediction_dict(finding.wall_clock_predicted),
        "citation": {"url": finding.citation.url, "source": finding.citation.source},
        "source_method": finding.source_method,
        "notes": list(finding.notes),
    }
