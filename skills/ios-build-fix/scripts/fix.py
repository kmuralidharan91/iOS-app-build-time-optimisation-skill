"""ios-build-fix orchestrator (Phase A).

Apply a single approved finding from ios-build-diagnose to an Xcode
project on a throwaway branch, re-measure with ios-build-measure, report
wall-clock delta, and refuse to claim success when the delta is null,
regressive, or within variance noise.

Single-fix-at-a-time; atomic git-aware (creates a branch named
``<branch_prefix>/<rule-slug>``); predicted-vs-actual logged for every
run.

Refusal taxonomy (mirrors fix-result.schema.json #/properties/outcome):

- ``success``                — at least one axis with a meaningful improvement.
- ``refused-null``           — relevant axis missing in measurement.
- ``refused-regressive``     — delta non-negative on every measured axis.
- ``refused-noise``          — |delta| within variance_threshold_percent on
                                every measured axis.
- ``refused-apply-error``    — fixer raised; tree reset.
- ``refused-benchmark-error``— pre or post benchmark crashed.

Exit codes:

- ``0``  on ``success`` or any ``refused-*`` outcome when ``--allow-refusal``.
- ``1``  on any ``refused-*`` outcome without ``--allow-refusal``.
- ``2``  on usage error / unregistered rule_id / missing artifact.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import shutil
import subprocess
import sys
import textwrap
from typing import Any

from fixers import AppliedFix, ApplyError, FixContext, to_applied_fix_dict
from fixers.registry import FixerSpec, UnregisteredFixer, resolve


__version__ = "0.1.0"
DEFAULT_VARIANCE_PCT = 10.0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    diagnosis = _load_json(args.diagnosis_artifact)
    simulation = _load_json(args.simulation_artifact) if args.simulation_artifact else None

    try:
        spec = resolve(args.rule_id)
    except UnregisteredFixer as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not spec.auto_apply and not args.allow_manual:
        print(
            f"error: rule_id={spec.rule_id} is informational in v1 "
            "(no auto-apply). Re-run with --allow-manual to acknowledge "
            "and emit a no-op fix-result for record-keeping.",
            file=sys.stderr,
        )
        return 2

    findings = _select_findings(diagnosis, args.rule_id)
    if not findings:
        print(
            f"error: no diagnose findings match rule_id={args.rule_id} "
            f"in {args.diagnosis_artifact}",
            file=sys.stderr,
        )
        return 2

    project_root = args.project_root.resolve()
    if not (project_root / ".git").exists() and not _is_git_worktree(project_root):
        print(
            f"error: project_root {project_root} is not a git repo / worktree",
            file=sys.stderr,
        )
        return 2

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    branch = _branch_name(args.branch_prefix, spec.rule_id)
    ctx = FixContext(
        diagnosis=diagnosis,
        simulation=simulation,
        project_root=project_root,
        branch=branch,
        auto_approve=args.auto_approve,
    )

    # ----- Approval gate ---------------------------------------------------

    preview_text = spec.preview(findings, ctx)
    print("===== ios-build-fix preview =====")
    print(f"rule_id: {spec.rule_id}  ({spec.family})")
    print(f"project_root: {project_root}")
    print(f"branch: {branch}")
    print(f"diagnose findings: {len(findings)}")
    if simulation is not None:
        predicted = _predicted_for(simulation, spec.rule_id)
        if predicted:
            print(
                f"predicted Δ: clean={predicted.get('clean')} "
                f"incremental={predicted.get('incremental')}"
            )
    print()
    print(preview_text)
    print("==================================")

    if not args.auto_approve:
        try:
            answer = input("Apply this fix? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted by user (no approval).", file=sys.stderr)
            return 2

    # ----- Branch create ---------------------------------------------------

    sha_before = _git_head(project_root)
    _ensure_branch(project_root, branch)

    # ----- Pre-fix measurement --------------------------------------------

    if args.reuse_measurement_pre is not None:
        measurement_pre_path = args.reuse_measurement_pre.resolve()
        if not measurement_pre_path.is_file():
            print(
                f"error: --reuse-measurement-pre {measurement_pre_path} not found",
                file=sys.stderr,
            )
            _git_reset(project_root, sha_before, branch)
            return 2
        measurement_pre = _load_json(measurement_pre_path)
        print(f"[fix.py] reusing pre-fix measurement: {measurement_pre_path}")
    else:
        measurement_pre_dir = output_dir / "measurement-pre"
        measurement_pre_dir.mkdir(parents=True, exist_ok=True)
        try:
            _run_benchmark(
                project_root=project_root,
                output_dir=measurement_pre_dir,
                build_types=args.build_types,
                touch_file=args.touch_file,
                repeats=args.repeats,
                scheme=args.scheme,
                configuration=args.configuration,
                destination=args.destination,
            )
        except subprocess.CalledProcessError as exc:
            _git_reset(project_root, sha_before, branch)
            return _emit_refusal(
                output_dir=output_dir,
                args=args,
                spec=spec,
                findings=findings,
                applied=None,
                measurement_pre=None,
                measurement_post=None,
                outcome="refused-benchmark-error",
                outcome_reason=f"pre-fix benchmark failed: exit {exc.returncode}",
                tuning_data_point=(
                    f"{spec.rule_id}: pre-fix benchmark crashed with exit "
                    f"{exc.returncode} on {project_root} (branch={branch})"
                ),
            )
        measurement_pre_path = measurement_pre_dir / "measurement.json"
        measurement_pre = _load_json(measurement_pre_path)

    # ----- Apply fix -------------------------------------------------------

    try:
        applied = spec.apply(findings, ctx)
    except ApplyError as exc:
        _git_reset(project_root, sha_before, branch)
        return _emit_refusal(
            output_dir=output_dir,
            args=args,
            spec=spec,
            findings=findings,
            applied=None,
            measurement_pre=_make_measurement_ref(measurement_pre_path, measurement_pre),
            measurement_post=None,
            outcome="refused-apply-error",
            outcome_reason=f"fixer raised ApplyError: {exc}",
            tuning_data_point=(
                f"{spec.rule_id}: ApplyError -> tree reset; revisit fixer for "
                f"{exc}"
            ),
        )

    if applied.kind == "no-op":
        # Informational fixers come here. Treat as refused-null because no
        # measurable change can result. Branch left empty.
        return _emit_refusal(
            output_dir=output_dir,
            args=args,
            spec=spec,
            findings=findings,
            applied=applied,
            measurement_pre=_make_measurement_ref(measurement_pre_path, measurement_pre),
            measurement_post=None,
            outcome="refused-null",
            outcome_reason=(
                f"fixer is informational (auto_apply={spec.auto_apply}); "
                f"no project mutation. Note: {applied.notes}"
            ),
            tuning_data_point=(
                f"{spec.rule_id}: informational no-op fixer ran; manual "
                "follow-up emitted in preview"
            ),
        )

    # ----- Post-fix measurement -------------------------------------------

    measurement_post_dir = output_dir / "measurement-post"
    measurement_post_dir.mkdir(parents=True, exist_ok=True)
    try:
        _run_benchmark(
            project_root=project_root,
            output_dir=measurement_post_dir,
            build_types=args.build_types,
            touch_file=args.touch_file,
            repeats=args.repeats,
            scheme=args.scheme,
            configuration=args.configuration,
            destination=args.destination,
        )
    except subprocess.CalledProcessError as exc:
        _git_reset(project_root, sha_before, branch)
        return _emit_refusal(
            output_dir=output_dir,
            args=args,
            spec=spec,
            findings=findings,
            applied=applied,
            measurement_pre=_make_measurement_ref(measurement_pre_path, measurement_pre),
            measurement_post=None,
            outcome="refused-benchmark-error",
            outcome_reason=f"post-fix benchmark failed: exit {exc.returncode}",
            tuning_data_point=(
                f"{spec.rule_id}: post-fix benchmark crashed; tree reset"
            ),
        )

    measurement_post_path = measurement_post_dir / "measurement.json"
    measurement_post = _load_json(measurement_post_path)

    # ----- Compute deltas + outcome ---------------------------------------

    actual_delta = _compute_delta(
        pre=measurement_pre,
        post=measurement_post,
        variance_threshold_pct=args.variance_threshold_pct,
        predicted=_predicted_for(simulation, spec.rule_id) if simulation else None,
    )
    outcome, outcome_reason = _decide_outcome(
        actual_delta=actual_delta,
        variance_threshold_pct=args.variance_threshold_pct,
    )

    tuning_data_point = _build_tuning_data_point(
        spec=spec,
        actual_delta=actual_delta,
        predicted=_predicted_for(simulation, spec.rule_id) if simulation else None,
        sha_before=sha_before,
        branch=branch,
    )

    # ----- Persist + return -----------------------------------------------

    record = _build_record(
        args=args,
        spec=spec,
        findings=findings,
        applied=applied,
        measurement_pre_ref=_make_measurement_ref(measurement_pre_path, measurement_pre),
        measurement_post_ref=_make_measurement_ref(measurement_post_path, measurement_post),
        actual_delta=actual_delta,
        outcome=outcome,
        outcome_reason=outcome_reason,
        tuning_data_point=tuning_data_point,
        notes=[],
    )
    fix_result_path = output_dir / "fix-result.json"
    fix_result_path.write_text(json.dumps(record, indent=2) + "\n")

    _print_summary(record)

    if outcome == "success":
        return 0
    if args.allow_refusal:
        return 0
    return 1


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fix.py",
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--diagnosis-artifact", type=pathlib.Path, required=True)
    parser.add_argument("--simulation-artifact", type=pathlib.Path, default=None)
    parser.add_argument("--rule-id", required=True,
        help="rule_id from ios-build-diagnose (e.g. script-phase/random-sleep)."
    )
    parser.add_argument("--project-root", type=pathlib.Path, required=True)
    parser.add_argument("--branch-prefix", default="gh-skill-test")
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--auto-approve", action="store_true",
        help="Skip the [y/N] prompt; required for non-interactive use."
    )
    parser.add_argument("--allow-refusal", action="store_true",
        help="Exit 0 even when outcome is refused-*. Used for the F9 designed "
             "null-delta refusal-path test.",
    )
    parser.add_argument("--allow-manual", action="store_true",
        help="Allow informational rule_ids (F5/F6/F7) to no-op-apply. Without "
             "this, fix.py refuses to run them.",
    )
    parser.add_argument("--variance-threshold-pct", type=float,
        default=DEFAULT_VARIANCE_PCT,
        help=(
            f"Default {DEFAULT_VARIANCE_PCT}%% (matches Phase A measure-gate "
            "spec). |delta| below this percent of baseline_median is "
            "classified as noise."
        ),
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--build-types", default="incremental",
        help="Comma-separated; clean and/or incremental. Default: incremental."
    )
    parser.add_argument("--touch-file", type=pathlib.Path, default=None,
        help="Required when incremental is in --build-types.",
    )
    parser.add_argument("--scheme", default="Debug")
    parser.add_argument("--configuration", default="Debug")
    parser.add_argument("--destination", default="generic/platform=iOS Simulator")
    parser.add_argument("--reuse-measurement-pre", type=pathlib.Path, default=None,
        help="Path to a pre-existing measurement.json to use as the pre-fix "
             "baseline (skips one benchmark run). Caller is responsible for "
             "ensuring it was taken at the right git SHA on the same machine.",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path: pathlib.Path) -> dict[str, Any]:
    with path.open() as fh:
        return json.load(fh)


def _select_findings(
    diagnosis: dict[str, Any],
    rule_id: str,
) -> list[tuple[int, dict[str, Any]]]:
    out: list[tuple[int, dict[str, Any]]] = []
    for idx, finding in enumerate(diagnosis.get("findings", [])):
        if finding.get("rule_id") == rule_id:
            out.append((idx, finding))
    for ridx, rec in enumerate(diagnosis.get("additional_recommendations", [])):
        if rec.get("rule_id") == rule_id:
            out.append((-1 - ridx, rec))
    return out


def _branch_name(prefix: str, rule_id: str) -> str:
    slug = rule_id.replace("/", "-").replace("_", "-")
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}/{slug}-{stamp}"


def _is_git_worktree(path: pathlib.Path) -> bool:
    git_path = path / ".git"
    return git_path.is_file()  # worktree links use a .git file


def _git_head(path: pathlib.Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def _ensure_branch(project_root: pathlib.Path, branch: str) -> None:
    """Create + check out the throwaway branch from current HEAD.

    If the branch already exists (re-runs), check it out without
    discarding work. The orchestrator never deletes user data.
    """

    rc = subprocess.call(
        ["git", "-C", str(project_root), "rev-parse", "--verify", branch],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if rc == 0:
        subprocess.check_call(
            ["git", "-C", str(project_root), "checkout", branch]
        )
    else:
        subprocess.check_call(
            ["git", "-C", str(project_root), "checkout", "-b", branch]
        )


def _git_reset(
    project_root: pathlib.Path,
    sha_before: str,
    branch: str,
) -> None:
    subprocess.call(
        ["git", "-C", str(project_root), "reset", "--hard", sha_before],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _run_benchmark(
    *,
    project_root: pathlib.Path,
    output_dir: pathlib.Path,
    build_types: str,
    touch_file: pathlib.Path | None,
    repeats: int,
    scheme: str,
    configuration: str,
    destination: str,
) -> None:
    benchmark_path = pathlib.Path(__file__).resolve().parent / "benchmark.py"
    cmd = [
        sys.executable,
        str(benchmark_path),
        "--project-path", str(project_root),
        "--scheme", scheme,
        "--configuration", configuration,
        "--destination", destination,
        "--repeats", str(repeats),
        "--build-types", build_types,
        "--output-dir", str(output_dir),
    ]
    if "incremental" in build_types and touch_file is not None:
        cmd.extend(["--touch-file", str(touch_file)])
    print(f"[fix.py] running benchmark: {' '.join(cmd)}")
    subprocess.check_call(cmd)


def _predicted_for(
    simulation: dict[str, Any] | None,
    rule_id: str,
) -> dict[str, Any] | None:
    if not simulation:
        return None
    for prediction in simulation.get("predictions", []):
        if prediction.get("rule_id") == rule_id:
            return {
                "clean": prediction.get("clean", {}).get("estimate_seconds"),
                "incremental": prediction.get("incremental", {}).get("estimate_seconds"),
            }
    return None


def _compute_delta(
    *,
    pre: dict[str, Any],
    post: dict[str, Any],
    variance_threshold_pct: float,
    predicted: dict[str, Any] | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for axis in ("clean", "incremental"):
        pre_summary = (pre.get("summary") or {}).get(axis) or {}
        post_summary = (post.get("summary") or {}).get(axis) or {}
        pre_med = pre_summary.get("median_seconds")
        post_med = post_summary.get("median_seconds")
        delta: float | None
        if isinstance(pre_med, (int, float)) and isinstance(post_med, (int, float)):
            delta = float(post_med) - float(pre_med)
        else:
            delta = None
        baseline = max(
            float(pre_med) if isinstance(pre_med, (int, float)) else 0.0,
            float(post_med) if isinstance(post_med, (int, float)) else 0.0,
        )
        exceeds_variance = (
            delta is not None
            and baseline > 0
            and abs(delta) > (variance_threshold_pct / 100.0) * baseline
        )
        within_band: bool | None = None
        predicted_axis = (predicted or {}).get(axis) if predicted else None
        if delta is not None and predicted_axis is not None:
            tolerance = 0.5 * abs(predicted_axis)
            if predicted_axis == 0:
                within_band = abs(delta) <= variance_threshold_pct / 100.0 * baseline
            else:
                within_band = abs(delta - predicted_axis) <= tolerance
        out[axis] = {
            "delta_seconds": delta,
            "baseline_median_seconds": (
                float(pre_med) if isinstance(pre_med, (int, float)) else None
            ),
            "post_median_seconds": (
                float(post_med) if isinstance(post_med, (int, float)) else None
            ),
            "spread_pre_percent": pre_summary.get("spread_percent"),
            "spread_post_percent": post_summary.get("spread_percent"),
            "exceeds_variance": bool(exceeds_variance),
            "within_predicted_band": within_band,
        }
    return out


def _decide_outcome(
    *,
    actual_delta: dict[str, Any],
    variance_threshold_pct: float,
) -> tuple[str, str]:
    """Convert the per-axis delta into a single outcome + reason.

    Policy:
    - If both axes have ``delta_seconds is None`` → ``refused-null``.
    - If at least one axis has ``delta_seconds < 0`` AND ``exceeds_variance`` →
      ``success`` (a single-axis win is enough; the demo is honest about
      which axis improved).
    - If every measured axis has ``delta_seconds >= 0`` → ``refused-regressive``.
    - Otherwise (every measured axis under variance noise) → ``refused-noise``.
    """

    measured = [
        axis for axis in ("clean", "incremental")
        if actual_delta[axis]["delta_seconds"] is not None
    ]
    if not measured:
        return (
            "refused-null",
            "no measurable axis: both clean and incremental delta_seconds are None",
        )
    wins = [
        axis for axis in measured
        if actual_delta[axis]["delta_seconds"] is not None
        and actual_delta[axis]["delta_seconds"] < 0
        and actual_delta[axis]["exceeds_variance"]
    ]
    if wins:
        win_axis = wins[0]
        d = actual_delta[win_axis]
        return (
            "success",
            f"{win_axis} delta {d['delta_seconds']:+.2f}s on "
            f"{d['baseline_median_seconds']:.2f}s baseline exceeds "
            f"{variance_threshold_pct:.1f}% variance threshold",
        )
    if all(
        actual_delta[axis]["delta_seconds"] is not None
        and actual_delta[axis]["delta_seconds"] >= 0
        for axis in measured
    ):
        bits = [
            f"{axis}={actual_delta[axis]['delta_seconds']:+.2f}s"
            for axis in measured
        ]
        return (
            "refused-regressive",
            "no axis improved: " + ", ".join(bits),
        )
    bits = [
        f"{axis}={actual_delta[axis]['delta_seconds']:+.2f}s "
        f"(within {variance_threshold_pct:.1f}% variance of "
        f"{max(actual_delta[axis]['baseline_median_seconds'] or 0, actual_delta[axis]['post_median_seconds'] or 0):.2f}s)"
        for axis in measured
    ]
    return (
        "refused-noise",
        "every measured axis under variance noise: " + "; ".join(bits),
    )


def _build_tuning_data_point(
    *,
    spec: FixerSpec,
    actual_delta: dict[str, Any],
    predicted: dict[str, Any] | None,
    sha_before: str,
    branch: str,
) -> str:
    parts = [f"{spec.rule_id} on {sha_before[:7]} (branch={branch})"]
    for axis in ("clean", "incremental"):
        d = actual_delta[axis]["delta_seconds"]
        b = actual_delta[axis]["baseline_median_seconds"]
        if d is not None and b is not None:
            within = actual_delta[axis]["within_predicted_band"]
            band_label = ""
            if predicted and (predicted.get(axis) is not None):
                band_label = (
                    f" (predicted {predicted[axis]:+.1f}s; "
                    f"within ±50%={within})"
                )
            parts.append(f"{axis} Δ {d:+.2f}s on {b:.2f}s baseline{band_label}")
    return "; ".join(parts)


def _make_measurement_ref(
    path: pathlib.Path,
    measurement: dict[str, Any],
) -> dict[str, Any]:
    summary = measurement.get("summary") or {}
    return {
        "path": str(path),
        "git_sha": (measurement.get("project") or {}).get("git_sha", ""),
        "schema_version": measurement.get("schema_version", "unknown"),
        "summary_clean_median_seconds": (summary.get("clean") or {}).get("median_seconds"),
        "summary_incremental_median_seconds": (summary.get("incremental") or {}).get("median_seconds"),
        "summary_clean_spread_percent": (summary.get("clean") or {}).get("spread_percent"),
        "summary_incremental_spread_percent": (summary.get("incremental") or {}).get("spread_percent"),
    }


def _build_record(
    *,
    args: argparse.Namespace,
    spec: FixerSpec,
    findings: list[tuple[int, dict[str, Any]]],
    applied: AppliedFix,
    measurement_pre_ref: dict[str, Any],
    measurement_post_ref: dict[str, Any] | None,
    actual_delta: dict[str, Any],
    outcome: str,
    outcome_reason: str,
    tuning_data_point: str,
    notes: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "tool": {"name": "ios-build-fix", "version": __version__},
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "inputs": {
            "diagnosis_artifact_path": str(args.diagnosis_artifact),
            "simulation_artifact_path": (
                str(args.simulation_artifact) if args.simulation_artifact else None
            ),
            "rule_id": spec.rule_id,
            "project_root": str(args.project_root.resolve()),
            "branch": _branch_name(args.branch_prefix, spec.rule_id),
            "auto_approve": args.auto_approve,
            "allow_refusal": args.allow_refusal,
            "reuse_measurement_pre_path": (
                str(args.reuse_measurement_pre) if args.reuse_measurement_pre else None
            ),
        },
        "target": {
            "rule_id": spec.rule_id,
            "family": spec.family,
            "source_finding_indices": [idx for idx, _f in findings],
            "predicted": _predicted_block(args.simulation_artifact, spec.rule_id),
        },
        "applied_fix": to_applied_fix_dict(applied),
        "measurement_pre": measurement_pre_ref,
        "measurement_post": measurement_post_ref or _empty_measurement_ref(),
        "actual_delta": actual_delta,
        "variance_threshold_percent": float(args.variance_threshold_pct),
        "outcome": outcome,
        "outcome_reason": outcome_reason,
        "tuning_data_point": tuning_data_point,
        "notes": notes,
    }


def _predicted_block(simulation_artifact: pathlib.Path | None, rule_id: str) -> dict[str, Any]:
    if simulation_artifact is None:
        return {
            "clean": {"delta_seconds": None},
            "incremental": {"delta_seconds": None},
        }
    sim = _load_json(simulation_artifact)
    pred = _predicted_for(sim, rule_id) or {}
    return {
        "clean": {"delta_seconds": pred.get("clean")},
        "incremental": {"delta_seconds": pred.get("incremental")},
    }


def _empty_measurement_ref() -> dict[str, Any]:
    return {
        "path": "",
        "git_sha": "",
        "schema_version": "unknown",
        "summary_clean_median_seconds": None,
        "summary_incremental_median_seconds": None,
        "summary_clean_spread_percent": None,
        "summary_incremental_spread_percent": None,
    }


def _emit_refusal(
    *,
    output_dir: pathlib.Path,
    args: argparse.Namespace,
    spec: FixerSpec,
    findings: list[tuple[int, dict[str, Any]]],
    applied: AppliedFix | None,
    measurement_pre: dict[str, Any] | None,
    measurement_post: dict[str, Any] | None,
    outcome: str,
    outcome_reason: str,
    tuning_data_point: str,
) -> int:
    if applied is None:
        applied = AppliedFix(
            kind="no-op",
            files_modified=(),
            git_sha_before="",
            git_sha_after="",
            submodule_changes=(),
            notes="apply step never reached or rolled back",
        )
    record = _build_record(
        args=args,
        spec=spec,
        findings=findings,
        applied=applied,
        measurement_pre_ref=measurement_pre or _empty_measurement_ref(),
        measurement_post_ref=measurement_post,
        actual_delta=_empty_delta(),
        outcome=outcome,
        outcome_reason=outcome_reason,
        tuning_data_point=tuning_data_point,
        notes=[],
    )
    fix_result_path = output_dir / "fix-result.json"
    fix_result_path.write_text(json.dumps(record, indent=2) + "\n")
    _print_summary(record)
    if args.allow_refusal:
        return 0
    return 1


def _empty_delta() -> dict[str, Any]:
    axis = {
        "delta_seconds": None,
        "baseline_median_seconds": None,
        "post_median_seconds": None,
        "spread_pre_percent": None,
        "spread_post_percent": None,
        "exceeds_variance": False,
        "within_predicted_band": None,
    }
    return {"clean": dict(axis), "incremental": dict(axis)}


def _print_summary(record: dict[str, Any]) -> None:
    print()
    print("===== ios-build-fix result =====")
    print(f"rule_id: {record['target']['rule_id']}")
    print(f"branch:  {record['inputs']['branch']}")
    pred = record["target"]["predicted"]
    delta = record["actual_delta"]
    for axis in ("clean", "incremental"):
        p = pred[axis]["delta_seconds"]
        d = delta[axis]["delta_seconds"]
        b = delta[axis]["baseline_median_seconds"]
        if d is None:
            print(f"  {axis:11s}  predicted={_fmt(p)}  actual=N/A")
            continue
        print(
            f"  {axis:11s}  predicted={_fmt(p)}  actual={d:+.2f}s on "
            f"{b:.2f}s  exceeds_variance={delta[axis]['exceeds_variance']}"
        )
    print(f"outcome: {record['outcome']}  ({record['outcome_reason']})")
    print("================================")


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}s"


if __name__ == "__main__":
    sys.exit(main())
