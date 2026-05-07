#!/usr/bin/env python3
"""ios-build-simulate CLI — predict Δ wall-clock per diagnose rule.

Reads a ``diagnosis.json`` (and optionally a ``measurement.json`` for
project-context-aware predictions) and emits a
JSON artifact conforming to ``schemas/simulation.schema.json``. Each
prediction aggregates findings sharing a rule_id into ONE
``RulePrediction`` and carries a ``tuning_data_point`` on both clean and
incremental axes per AGENTS.md non-negotiable principle 5.

This skill never invokes xcodebuild and never edits project files.

Usage:

    python3 scripts/simulate.py \\
        --diagnosis-artifact docs/smoke/2/diagnosis.json \\
        --measurement-artifact docs/smoke/1/measurement.json \\
        --output-dir docs/smoke/3/

Output: ``<output-dir>/simulation.json`` plus stdout summary.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

# Allow running as ``python3 scripts/simulate.py`` from the repo root
# without having to install the package.
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    sys.path.insert(0, str(_REPO_ROOT))

from simulators import (  # noqa: E402
    RulePrediction,
    SimulationContext,
    baseline_clean_seconds_from_measurement,
    baseline_incremental_seconds_from_measurement,
    to_rule_prediction_dict,
)
from simulators import registry as registry_module  # noqa: E402


_TOOL_NAME = "ios-build-simulate"
_TOOL_VERSION = "0.1.0"


def _git_sha(project_path: pathlib.Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _git_branch(project_path: pathlib.Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_path), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        branch = completed.stdout.strip()
        if branch != "HEAD":
            return branch
        sha = _git_sha(project_path)
        if not sha:
            return ""
        completed = subprocess.run(
            [
                "git", "-C", str(project_path), "for-each-ref",
                "--points-at", sha,
                "--format=%(refname:short)",
                "refs/remotes/",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        for ref in completed.stdout.splitlines():
            ref = ref.strip()
            if not ref or ref.endswith("/HEAD"):
                continue
            if "/" in ref:
                return ref.split("/", 1)[1]
        return ""
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _load_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _maybe_load_json(path: pathlib.Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        return _load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"[simulate] could not read {path}: {exc}",
            file=sys.stderr,
        )
        return None


def _bucket_findings_by_rule(
    diagnosis: dict[str, Any],
) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    """Group diagnosis findings + additional_recommendations by rule_id.

    Returns a dict keyed by rule_id whose values are lists of
    (original_index, finding_dict) tuples. Additional_recommendations
    are interleaved (the simulator treats them the same way as findings;
    the diagnosis's distinction matters for the F1-F9 recall denominator
    but not for predicted Δ).
    """

    buckets: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    findings = diagnosis.get("findings") or []
    for idx, finding in enumerate(findings):
        rule_id = finding.get("rule_id")
        if not rule_id:
            continue
        buckets.setdefault(rule_id, []).append((idx, finding))
    # Additional recommendations live in their own array; index them
    # separately so the artifact's source_findings.indices stays
    # interpretable. We use negative indices (-1 - original_index) to
    # signal "this came from additional_recommendations[]".
    recs = diagnosis.get("additional_recommendations") or []
    for idx, rec in enumerate(recs):
        rule_id = rec.get("rule_id")
        if not rule_id:
            continue
        buckets.setdefault(rule_id, []).append((-1 - idx, rec))
    return buckets


def _rank(predictions: list[RulePrediction]) -> list[RulePrediction]:
    """Most-improvement-first by clean+incremental sum."""

    def sort_key(p: RulePrediction) -> float:
        clean = p.clean.estimate_seconds or 0.0
        inc = p.incremental.estimate_seconds or 0.0
        return clean + inc  # most negative first

    return sorted(predictions, key=sort_key)


def _summary(predictions: list[RulePrediction]) -> dict[str, Any]:
    total_clean = sum((p.clean.estimate_seconds or 0.0) for p in predictions)
    total_inc = sum((p.incremental.estimate_seconds or 0.0) for p in predictions)
    by_clean = sorted(
        predictions,
        key=lambda p: (p.clean.estimate_seconds or 0.0),
    )
    by_inc = sorted(
        predictions,
        key=lambda p: (p.incremental.estimate_seconds or 0.0),
    )
    return {
        "total_predicted_clean_seconds": total_clean,
        "total_predicted_incremental_seconds": total_inc,
        "top_3_by_clean": [p.rule_id for p in by_clean[:3]],
        "top_3_by_incremental": [p.rule_id for p in by_inc[:3]],
    }


def simulate(
    diagnosis_artifact: pathlib.Path,
    measurement_artifact: pathlib.Path | None,
    output_dir: pathlib.Path,
    f6_verified: bool,
) -> dict[str, Any]:
    """Run the simulate pipeline and return the artifact dict."""

    output_dir.mkdir(parents=True, exist_ok=True)

    diagnosis = _load_json(diagnosis_artifact)
    measurement = _maybe_load_json(measurement_artifact)

    project_path = pathlib.Path(diagnosis.get("project", {}).get("path", "."))

    ctx = SimulationContext(
        diagnosis=diagnosis,
        measurement=measurement,
        project_path=project_path,
        baseline_clean_seconds=baseline_clean_seconds_from_measurement(measurement),
        baseline_incremental_seconds=baseline_incremental_seconds_from_measurement(
            measurement
        ),
    )

    buckets = _bucket_findings_by_rule(diagnosis)
    rule_registry = registry_module.build_registry(f6_verified=f6_verified)

    notes: list[str] = []
    if measurement is None:
        notes.append(
            "No --measurement-artifact supplied; predictors that consume "
            "it (compilation-cache, asset-catalog, oversized-module) fall "
            "back to reference data with reduced confidence."
        )
    if not f6_verified:
        notes.append(
            "F6 (spm/swift-syntax-not-prebuilt) prediction is best-effort: "
            "Xcode 26 prebuilt-swift-syntax mechanism UNVERIFIED at line "
            "level (deferred). See references/sources.md."
        )

    predictions: list[RulePrediction] = []
    unhandled_rule_ids: list[str] = []
    for rule_id, findings in buckets.items():
        predictor = rule_registry.get(rule_id)
        if predictor is None:
            unhandled_rule_ids.append(rule_id)
            predictions.append(
                registry_module.predict_unknown(rule_id, findings, ctx)
            )
            continue
        predictions.append(predictor(findings, ctx))

    if unhandled_rule_ids:
        notes.append(
            "rule_id(s) without registered predictor (synthesised "
            "placeholder predictions): "
            f"{', '.join(sorted(set(unhandled_rule_ids)))}"
        )

    predictions = _rank(predictions)

    artifact: dict[str, Any] = {
        "schema_version": "1.0.0",
        "tool": {"name": _TOOL_NAME, "version": _TOOL_VERSION},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": {
            "diagnosis_artifact_path": str(diagnosis_artifact),
            "measurement_artifact_path": (
                str(measurement_artifact) if measurement_artifact else None
            ),
            "git_sha": _git_sha(project_path),
            "git_branch": _git_branch(project_path),
        },
        "predictions": [to_rule_prediction_dict(p) for p in predictions],
        "summary": _summary(predictions),
        "notes": notes,
    }
    return artifact


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ios-build-simulate",
        description=(
            "Predict Δ wall-clock per diagnose rule. Read alongside the "
            "diagnosis artifact whose findings drove each prediction. "
            "Predictions are per-rule (aggregating multiple same-rule "
            "findings), labelled 'predicted', NEVER 'measured'."
        ),
    )
    parser.add_argument(
        "--diagnosis-artifact", required=True, type=pathlib.Path,
        help="Path to a diagnosis.json (ios-build-diagnose output).",
    )
    parser.add_argument(
        "--measurement-artifact", default=None, type=pathlib.Path,
        help=(
            "Optional path to a measurement.json. Predictors that "
            "consume baseline timings (F4 compilation-cache, F5 "
            "asset-catalog, F7 oversized-module) use it when supplied."
        ),
    )
    parser.add_argument(
        "--output-dir", required=True, type=pathlib.Path,
        help="Where simulation.json + logs are written.",
    )
    parser.add_argument(
        "--f6-verified", action="store_true",
        help=(
            "Set when the deferred verify confirms the Xcode 26 "
            "prebuilt-swift-syntax mechanism at line level. Affects the "
            "F6 prediction's tuning_data_point text."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    artifact = simulate(
        diagnosis_artifact=args.diagnosis_artifact,
        measurement_artifact=args.measurement_artifact,
        output_dir=args.output_dir,
        f6_verified=args.f6_verified,
    )

    out_path = args.output_dir / "simulation.json"
    out_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    summary = artifact["summary"]
    print(
        f"[simulate] wrote {out_path} — "
        f"{len(artifact['predictions'])} predictions; "
        f"total predicted clean Δ = {summary['total_predicted_clean_seconds']:.1f}s; "
        f"total predicted incremental Δ = {summary['total_predicted_incremental_seconds']:.1f}s"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
