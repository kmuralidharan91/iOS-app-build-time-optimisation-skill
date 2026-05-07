"""Asset-catalog analyzer — covers F5 (CompileAssetCatalogVariant on incremental).

Reads the benchmark ``measurement.json`` artifact's ``critical_path``
section. When ``CompileAssetCatalogVariant`` is in the incremental
build's ranked task-class list with a total duration above the
``asset-catalog/incremental >= 3s`` threshold from
``references/defaults.md``, we emit a Finding.

The threshold is sized against a private-corpus baseline measurement
on incremental Debug builds. TODO(public-cite: NetNewsWire) confirm
the threshold against a public iOS project.

Rule id: ``asset-catalog/incremental-recompile``.
"""

from __future__ import annotations

from . import (
    Citation,
    DiagnosisContext,
    Evidence,
    Finding,
    WallClockPrediction,
)


_INCREMENTAL_THRESHOLD_SECONDS = 3.0
_TASK_CLASS_NAME = "CompileAssetCatalogVariant"
_APPLE_ACTOOL_URL = (
    "https://developer.apple.com/documentation/xcode/asset-management"
)


def run(context: DiagnosisContext) -> list[Finding]:
    measurement = context.measurement or {}
    critical_path = measurement.get("critical_path") or {}
    incremental = critical_path.get("incremental") or {}
    nodes = incremental.get("nodes") or []

    target_node = next(
        (
            node for node in nodes
            if node.get("dominant_task") == _TASK_CLASS_NAME
            or node.get("class_name") == _TASK_CLASS_NAME
        ),
        None,
    )
    if target_node is None:
        return []

    duration_seconds = float(
        target_node.get("duration_seconds")
        or target_node.get("total_seconds")
        or 0.0
    )
    if duration_seconds < _INCREMENTAL_THRESHOLD_SECONDS:
        return []

    measurement_path = (
        context.measurement
        and context.measurement.get("_artifact_path")
    ) or "<measurement.json>"

    return [
        Finding(
            rule_id="asset-catalog/incremental-recompile",
            family="asset-catalog",
            title=(
                f"{_TASK_CLASS_NAME} runs ~{duration_seconds:.1f}s on every "
                "incremental build"
            ),
            evidence=Evidence(
                kind="measurement",
                path=str(measurement_path),
                key=_TASK_CLASS_NAME,
                value=f"total_seconds={duration_seconds:.3f}",
                configuration=context.configuration,
            ),
            impact_category="high" if duration_seconds >= 6.0 else "medium",
            wall_clock_predicted=WallClockPrediction(
                method="measured-on-private-corpus",
                estimate_seconds=duration_seconds,
                min_seconds=max(duration_seconds - 2.0, 0.0),
                max_seconds=duration_seconds,
                notes=(
                    "Asset catalog should be cached when inputs are "
                    "unchanged; non-zero incremental cost almost always "
                    "traces back to an upstream input invalidation, often "
                    "a script phase that mutates xcassets contents on "
                    "every build. TODO(public-cite: NetNewsWire) confirm "
                    "the equivalent root-cause script phase if the public "
                    "project exhibits the same pattern."
                ),
            ),
            citation=Citation(
                url=_APPLE_ACTOOL_URL,
                source="Apple — Asset Management (actool reference)",
            ),
            source_method="timing-summary",
            notes=(
                f"Threshold for surfacing: total_seconds >= {_INCREMENTAL_THRESHOLD_SECONDS}s "
                "(see references/defaults.md).",
            ),
        )
    ]
