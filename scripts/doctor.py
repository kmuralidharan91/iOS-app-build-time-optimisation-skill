#!/usr/bin/env python3
"""ios-build-doctor orchestrator (Phase A).

Drive the four prior skills end-to-end:

    questionnaire -> adapter detection -> measure -> diagnose -> simulate
    -> top-N approval prompt -> fix on a throwaway worktree -> re-measure
    -> single transcript artifact

This module is the conversational entrypoint behind
``skills/ios-build-doctor/SKILL.md``. It calls each sibling CLI as a
subprocess (no in-process function composition) so coupling stays at the
JSON-artifact boundary; downstream regressions never silently break the
production-ready ``scripts/fix.py``.

Outcome enum is a strict superset of fix.py's:

    success | refused-null | refused-regressive | refused-noise
        | refused-apply-error | refused-benchmark-error      (verbatim from fix.py)
    abort:non-xcode-v1-fence | abort:measure-failed
        | abort:diagnose-failed | abort:simulate-failed
        | abort:no-actionable | abort:fix-failed
        | abort:worktree-failed                              (doctor-only)
    info:user-declined | info:baseline-only                  (doctor-only)

Exit codes:

    0 -- success, refused-* (refusal is honest-PASS in demo terms),
         info:* (user choice / baseline-only short-circuit),
         abort:non-xcode-v1-fence (fence firing is itself a successful run)
    1 -- any other abort:* outcome
    2 -- usage error / argparse failure
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from adapters import detect_build_system  # noqa: E402
import fix as _fix_module  # noqa: E402
from fixers.registry import build_registry  # noqa: E402


__version__ = "0.1.0"
DEFAULT_VARIANCE_PCT = 10.0
DEFAULT_TOP_N = 3
DEFAULT_BUILD_TYPES = "clean,incremental"
DEFAULT_REPEATS = 3
DEFAULT_DESTINATION = "generic/platform=iOS Simulator"
DEFAULT_CONFIGURATION = "Debug"
DEFAULT_BRANCH_PREFIX = "chore"
DEFAULT_WORKTREE_BASE = "/tmp"


# ---------------------------------------------------------------------------
# DoctorContext
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class DoctorContext:
    # questionnaire-resolved fields (Q1..Q8)
    project_path: pathlib.Path
    build_system: str  # detection result; v1 fence enforces "xcode"
    build_types: str  # Q3
    configuration: str  # Q4
    scheme: str  # Q5
    destination: str  # Q6
    repeats: int  # Q7
    touch_file: pathlib.Path | None  # Q7
    goal: str  # Q8: baseline | find | apply
    # CLI-only knobs
    output_dir: pathlib.Path
    top_n: int
    worktree_base: pathlib.Path
    branch_prefix: str
    variance_threshold_pct: float
    auto_approve_fix: bool
    allow_manual: bool
    rule_id: str | None
    non_interactive: bool
    transcript_path: pathlib.Path
    keep_worktree: bool
    no_verify_commits: bool
    worktree_seed_ref: str
    # runtime state
    run_id: str = ""
    started_at: str = ""
    finished_at: str = ""


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="doctor.py",
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-path", type=pathlib.Path, required=True,
        help="Project root containing *.xcodeproj or *.xcworkspace.")
    parser.add_argument("--scheme", required=True,
        help="Xcode scheme to measure / diagnose / simulate.")
    parser.add_argument("--configuration", default=DEFAULT_CONFIGURATION,
        help="Build configuration name (default: Debug).")
    parser.add_argument("--destination", default=DEFAULT_DESTINATION,
        help="xcodebuild -destination string.")
    parser.add_argument("--touch-file", type=pathlib.Path, default=None,
        help="Required when 'incremental' is in --build-types.")
    parser.add_argument("--build-types", default=DEFAULT_BUILD_TYPES,
        help="Comma list of clean,incremental (default: clean,incremental).")
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS,
        help="Number of repeats per build type (default: 3).")
    parser.add_argument("--output-dir", type=pathlib.Path, required=True,
        help="Run-rooted output directory (e.g. docs/smoke/5/run-001/).")
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N,
        help="Number of ranked predictions surfaced for approval (default: 3).")
    parser.add_argument("--worktree-base", type=pathlib.Path,
        default=pathlib.Path(DEFAULT_WORKTREE_BASE),
        help="Parent directory for the throwaway worktree (default: /tmp).")
    parser.add_argument("--branch-prefix", default=DEFAULT_BRANCH_PREFIX,
        help="Forwarded to fix.py --branch-prefix.")
    parser.add_argument("--variance-threshold-pct", type=float,
        default=DEFAULT_VARIANCE_PCT,
        help=f"Default {DEFAULT_VARIANCE_PCT}%%; forwarded to measure + fix.")
    parser.add_argument("--auto-approve-fix", action="store_true",
        help="Forwarded as --auto-approve to fix.py.")
    parser.add_argument("--allow-manual", action="store_true",
        help="Always forwarded to fix.py regardless of picked rule (per "
             "Phase A plan decision: manual rules go through fix.py for "
             "tuning-data consistency).")
    parser.add_argument("--rule-id", default=None,
        help="Skip the top-N approval prompt and pre-pick this rule.")
    parser.add_argument("--non-interactive", action="store_true",
        help="Refuse if any required answer is missing from CLI flags.")
    parser.add_argument("--transcript-path", type=pathlib.Path, default=None,
        help="Override transcript path (default: <output-dir>/swiftcraft-loop.md).")
    parser.add_argument("--keep-worktree", action="store_true",
        help="Skip 'git worktree remove' for post-mortem inspection.")
    parser.add_argument("--worktree-seed-ref", default="develop",
        help="git ref to seed the throwaway worktree from (default: 'develop'). "
             "Use 'origin/develop' on hosts whose local develop branch is stale "
             "vs. the upstream pin (e.g. REDACTED Phase A/1/2/3/4 ground truth at "
             "origin/develop=REDACTED).")
    parser.add_argument("--no-verify-commits", action="store_true",
        help="Forwarded to fix.py for projects whose hooks gate on branch-name "
             "or ticket-name patterns rather than correctness.")
    parser.add_argument("--goal", default="find",
        choices=["baseline", "find", "apply"],
        help="Q8: baseline (measure only), find (full loop, default), apply "
             "(skip top-N prompt, requires --rule-id).")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Questionnaire + build-system fence
# ---------------------------------------------------------------------------


def _resolve_questionnaire(args: argparse.Namespace) -> DoctorContext:
    project_path = args.project_path.resolve()
    if not project_path.is_dir():
        raise SystemExit(
            f"--project-path {project_path} is not a directory"
        )

    if args.touch_file is not None:
        touch_file = args.touch_file.resolve()
    else:
        touch_file = None

    if "incremental" in args.build_types and touch_file is None:
        raise SystemExit(
            "--touch-file is required when 'incremental' is in --build-types"
        )

    if args.goal == "apply" and not args.rule_id:
        raise SystemExit(
            "--goal=apply requires --rule-id <rule>"
        )

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    transcript_path = (
        args.transcript_path.resolve()
        if args.transcript_path is not None
        else output_dir / "swiftcraft-loop.md"
    )

    return DoctorContext(
        project_path=project_path,
        build_system="",  # filled by _detect_or_confirm_build_system
        build_types=args.build_types,
        configuration=args.configuration,
        scheme=args.scheme,
        destination=args.destination,
        repeats=args.repeats,
        touch_file=touch_file,
        goal=args.goal,
        output_dir=output_dir,
        top_n=args.top_n,
        worktree_base=args.worktree_base.resolve(),
        branch_prefix=args.branch_prefix,
        variance_threshold_pct=args.variance_threshold_pct,
        auto_approve_fix=args.auto_approve_fix,
        allow_manual=args.allow_manual,
        rule_id=args.rule_id,
        non_interactive=args.non_interactive,
        transcript_path=transcript_path,
        keep_worktree=args.keep_worktree,
        no_verify_commits=args.no_verify_commits,
        worktree_seed_ref=args.worktree_seed_ref,
    )


def _detect_or_confirm_build_system(ctx: DoctorContext) -> str:
    """Return the detected build system; v1 fence applies to caller."""

    detected = detect_build_system(ctx.project_path)
    ctx.build_system = detected
    return detected


# ---------------------------------------------------------------------------
# Subprocess wrappers — measure / diagnose / simulate
# ---------------------------------------------------------------------------


def _run_measure(ctx: DoctorContext) -> tuple[int, pathlib.Path]:
    out = ctx.output_dir / "measurement"
    out.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = [
        sys.executable,
        str(_SCRIPTS_DIR / "benchmark.py"),
        "--project-path", str(ctx.project_path),
        "--scheme", ctx.scheme,
        "--configuration", ctx.configuration,
        "--destination", ctx.destination,
        "--repeats", str(ctx.repeats),
        "--build-types", ctx.build_types,
        "--variance-threshold", str(ctx.variance_threshold_pct),
        "--output-dir", str(out),
    ]
    if "incremental" in ctx.build_types and ctx.touch_file is not None:
        cmd.extend(["--touch-file", str(ctx.touch_file)])
    print(f"[doctor.py] measure: {' '.join(cmd)}")
    rc = subprocess.call(cmd)
    return rc, out / "measurement.json"


def _run_diagnose(
    ctx: DoctorContext,
    measurement_artifact: pathlib.Path,
) -> tuple[int, pathlib.Path]:
    out = ctx.output_dir / "diagnosis"
    out.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(_SCRIPTS_DIR / "diagnose.py"),
        "--project-path", str(ctx.project_path),
        "--scheme", ctx.scheme,
        "--configuration", ctx.configuration,
        "--destination", ctx.destination,
        "--measurement-artifact", str(measurement_artifact),
        "--output-dir", str(out),
    ]
    print(f"[doctor.py] diagnose: {' '.join(cmd)}")
    rc = subprocess.call(cmd)
    return rc, out / "diagnosis.json"


def _run_simulate(
    ctx: DoctorContext,
    diagnosis_artifact: pathlib.Path,
    measurement_artifact: pathlib.Path,
) -> tuple[int, pathlib.Path]:
    out = ctx.output_dir / "simulation"
    out.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(_SCRIPTS_DIR / "simulate.py"),
        "--diagnosis-artifact", str(diagnosis_artifact),
        "--measurement-artifact", str(measurement_artifact),
        "--output-dir", str(out),
    ]
    print(f"[doctor.py] simulate: {' '.join(cmd)}")
    rc = subprocess.call(cmd)
    return rc, out / "simulation.json"


# ---------------------------------------------------------------------------
# Prediction ranking + top-N prompt
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class RankedPrediction:
    rule_id: str
    family: str
    title: str
    auto_apply: bool
    clean_estimate: float | None
    incremental_estimate: float | None
    confidence: str
    rank_score: float


def _rank_predictions(
    simulation: dict[str, Any],
    show_all: bool = False,
) -> list[RankedPrediction]:
    registry = build_registry()
    predictions = simulation.get("predictions", []) or []
    ranked: list[RankedPrediction] = []
    for pred in predictions:
        rid = pred.get("rule_id", "")
        clean = (pred.get("clean") or {}).get("estimate_seconds")
        incr = (pred.get("incremental") or {}).get("estimate_seconds")

        spec = registry.get(rid)
        auto = bool(spec.auto_apply) if spec is not None else False

        magnitudes = []
        for v in (clean, incr):
            if isinstance(v, (int, float)):
                magnitudes.append(abs(float(v)))
        score = max(magnitudes) if magnitudes else 0.0

        # Hide rules with zero magnitude on both axes unless show_all.
        # Exception: F9 (eager-linking-disabled) is the designed null-delta
        # refusal-path test; keep it visible for demo purposes.
        is_f9 = rid == "build-setting/eager-linking-disabled"
        if not show_all and score == 0.0 and not is_f9:
            continue

        ranked.append(RankedPrediction(
            rule_id=rid,
            family=pred.get("family", ""),
            title=pred.get("title", ""),
            auto_apply=auto,
            clean_estimate=clean if isinstance(clean, (int, float)) else None,
            incremental_estimate=incr if isinstance(incr, (int, float)) else None,
            confidence=str(pred.get("confidence", "")),
            rank_score=score,
        ))

    # Auto-applicable first; then by rank_score descending.
    ranked.sort(key=lambda r: (0 if r.auto_apply else 1, -r.rank_score, r.rule_id))
    return ranked


def _format_top_n_table(ranked: list[RankedPrediction], top_n: int) -> str:
    lines: list[str] = []
    auto_count = sum(1 for r in ranked if r.auto_apply)
    rows = ranked[:top_n]
    lines.append("===== ios-build-doctor: top {} predicted improvements =====".format(len(rows)))
    lines.append("Pick one to apply, or 's' to skip.")
    lines.append("")
    for i, r in enumerate(rows, start=1):
        clean_s = (
            f"{r.clean_estimate:+.1f}s"
            if r.clean_estimate is not None else "n/a"
        )
        incr_s = (
            f"{r.incremental_estimate:+.1f}s"
            if r.incremental_estimate is not None else "n/a"
        )
        auto_label = "YES (auto)" if r.auto_apply else "NO  (manual recipe)"
        lines.append(
            f"  {i}. {r.rule_id}"
        )
        lines.append(
            f"       predicted: clean {clean_s}, incremental {incr_s}"
        )
        lines.append(
            f"       auto-apply: {auto_label}   confidence: {r.confidence}"
        )
        if r.title:
            lines.append(f"       title: {r.title}")
        lines.append("")
    if auto_count == 0:
        lines.append("(note: no auto-applicable rules in the predictions; "
                     "manual rules will go through fix.py with --allow-manual.)")
        lines.append("")
    return "\n".join(lines)


def _present_top_n_and_prompt(
    ctx: DoctorContext,
    ranked: list[RankedPrediction],
) -> tuple[str | None, RankedPrediction | None, str]:
    """Return (rule_id, ranked_entry, source_label).

    rule_id == None signals user declined (s) or no actionable rule.
    """

    if not ranked:
        print("[doctor.py] no actionable predictions surfaced.")
        return None, None, "no-actionable"

    if ctx.rule_id:
        for r in ranked:
            if r.rule_id == ctx.rule_id:
                print(_format_top_n_table(ranked, ctx.top_n))
                print(f"Pre-picked via --rule-id: {ctx.rule_id}")
                return ctx.rule_id, r, "--rule-id flag"
        # rule not in ranked: still allow it (user may want a non-top rule)
        print(_format_top_n_table(ranked, ctx.top_n))
        print(f"Pre-picked via --rule-id (not in top-{ctx.top_n}): {ctx.rule_id}")
        return ctx.rule_id, None, "--rule-id flag (outside top-N)"

    if ctx.non_interactive:
        raise SystemExit(
            "--non-interactive is set but --rule-id was not supplied; "
            "doctor cannot decide which rule to apply."
        )

    print(_format_top_n_table(ranked, ctx.top_n))
    valid_choices = list(range(1, min(len(ranked), ctx.top_n) + 1))
    prompt_str = "Enter choice [{} or s]: ".format("/".join(str(i) for i in valid_choices))
    try:
        answer = input(prompt_str)
    except EOFError:
        answer = ""
    answer = answer.strip().lower()
    if answer in ("s", "skip", ""):
        print("[doctor.py] user declined.")
        return None, None, "interactive prompt -> skip"
    try:
        idx = int(answer)
    except ValueError:
        raise SystemExit(f"unrecognized choice: {answer!r}")
    if idx < 1 or idx > len(ranked):
        raise SystemExit(f"choice {idx} out of range 1..{len(ranked)}")
    picked = ranked[idx - 1]
    return picked.rule_id, picked, "interactive prompt"


# ---------------------------------------------------------------------------
# Worktree setup + teardown
# ---------------------------------------------------------------------------


def _utc_stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _setup_worktree(
    ctx: DoctorContext,
    git_root: pathlib.Path,
    branch_seed: str = "develop",
) -> pathlib.Path:
    stamp = _utc_stamp()
    target = ctx.worktree_base / f"REDACTED-doctor-{stamp}"
    if target.exists():
        # Extremely unlikely (UTC-second resolution + REDACTED-doctor prefix).
        # Bail out rather than clobber.
        raise RuntimeError(f"worktree path already exists: {target}")

    print(f"[doctor.py] worktree add {target} from {git_root} ({branch_seed})")
    rc = subprocess.call([
        "git", "-C", str(git_root),
        "worktree", "add", "--detach", str(target), branch_seed,
    ])
    # Mirror Phase A fix.py._ensure_branch: foreign post-checkout hooks (e.g.
    # REDACTED's Swift-macro-trust + GitFlow-name checks) can return non-zero
    # while the worktree itself is fully created. Verify by post-condition
    # rather than trusting git's exit code.
    if rc != 0:
        if not (target.exists() and (target / ".git").exists()):
            raise RuntimeError(f"git worktree add failed (rc={rc}); see logs above")
        print(
            f"[doctor.py] note: git worktree add returned rc={rc} but worktree "
            f"directory + .git pointer exist at {target}; treating as success "
            f"(foreign post-checkout hook).",
            file=sys.stderr,
        )

    # Best-effort submodule init for projects like REDACTED.
    submodule_rc = subprocess.call(
        ["git", "-C", str(target), "submodule", "update", "--init", "--recursive"],
    )
    if submodule_rc != 0:
        print(
            f"[doctor.py] note: 'git submodule update --init --recursive' "
            f"exited rc={submodule_rc} in worktree {target}; "
            f"continuing (project may have no submodules).",
            file=sys.stderr,
        )
    return target


def _teardown_worktree(
    ctx: DoctorContext,
    git_root: pathlib.Path,
    worktree: pathlib.Path,
) -> None:
    if ctx.keep_worktree:
        print(f"[doctor.py] --keep-worktree set; leaving {worktree}")
        return
    print(f"[doctor.py] worktree remove --force {worktree}")
    rc = subprocess.call([
        "git", "-C", str(git_root),
        "worktree", "remove", "--force", str(worktree),
    ])
    if rc != 0:
        # As a fallback, prune + rmtree so the temp dir doesn't linger.
        print(
            f"[doctor.py] note: worktree remove rc={rc}; "
            f"trying rmtree + worktree prune as fallback",
            file=sys.stderr,
        )
        try:
            if worktree.exists():
                shutil.rmtree(worktree, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[doctor.py] rmtree failed: {exc}", file=sys.stderr)
        subprocess.call(
            ["git", "-C", str(git_root), "worktree", "prune"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


# ---------------------------------------------------------------------------
# Fix invocation
# ---------------------------------------------------------------------------


def _slugify_rule(rule_id: str) -> str:
    return rule_id.replace("/", "-").replace("_", "-")


def _run_fix(
    ctx: DoctorContext,
    rule_id: str,
    diagnosis_artifact: pathlib.Path,
    simulation_artifact: pathlib.Path,
    measurement_pre: pathlib.Path,
    worktree: pathlib.Path,
    touch_file_for_fix: pathlib.Path | None = None,
) -> tuple[int, pathlib.Path]:
    out = ctx.output_dir / f"fix-{_slugify_rule(rule_id)}"
    out.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = [
        sys.executable,
        str(_SCRIPTS_DIR / "fix.py"),
        "--diagnosis-artifact", str(diagnosis_artifact),
        "--simulation-artifact", str(simulation_artifact),
        "--rule-id", rule_id,
        "--project-root", str(worktree),
        "--branch-prefix", ctx.branch_prefix,
        "--output-dir", str(out),
        "--reuse-measurement-pre", str(measurement_pre),
        "--variance-threshold-pct", str(ctx.variance_threshold_pct),
        "--repeats", str(ctx.repeats),
        "--build-types", ctx.build_types,
        "--scheme", ctx.scheme,
        "--configuration", ctx.configuration,
        "--destination", ctx.destination,
        # Doctor always forwards these three; per Phase A plan:
        "--auto-approve",      # double-prompt avoidance
        "--allow-refusal",     # refused-* is honest-PASS for the demo
        "--allow-manual",      # manual rules go through fix.py for tuning data
    ]
    # Forward the worktree-translated touch-file (Phase A D.6) so
    # fix.py's post-fix benchmark mtime-touches a file inside the
    # throwaway worktree, not the primary checkout.
    fix_touch = touch_file_for_fix if touch_file_for_fix is not None else ctx.touch_file
    if "incremental" in ctx.build_types and fix_touch is not None:
        cmd.extend(["--touch-file", str(fix_touch)])
    if ctx.no_verify_commits:
        cmd.append("--no-verify-commits")

    print(f"[doctor.py] fix: {' '.join(cmd)}")
    rc = subprocess.call(cmd)
    return rc, out / "fix-result.json"


# ---------------------------------------------------------------------------
# Outcome + transcript
# ---------------------------------------------------------------------------


def _load_json(path: pathlib.Path) -> dict[str, Any]:
    with path.open() as fh:
        return json.load(fh)


def _gap_line(predicted: float | None, actual: float | None) -> str:
    if predicted is None or actual is None:
        return "n/a"
    gap = abs(predicted - actual)
    if abs(predicted) < 1e-9:
        # Predicted Δ is zero — use absolute tolerance and report it.
        return f"gap {gap:.3f}s (absolute; predicted=0.000s)"
    pct = 100.0 * gap / abs(predicted)
    return f"gap {gap:.3f}s ({pct:.3f}% error)"


def _read_actual(fix_result: dict[str, Any], axis: str) -> float | None:
    actual = fix_result.get("actual_delta") or {}
    block = actual.get(axis) or {}
    v = block.get("delta_seconds")
    return v if isinstance(v, (int, float)) else None


def _read_predicted(fix_result: dict[str, Any], axis: str) -> float | None:
    target = fix_result.get("target") or {}
    pred = (target.get("predicted") or {}).get(axis) or {}
    v = pred.get("delta_seconds")
    return v if isinstance(v, (int, float)) else None


def _summary_median(measurement: dict[str, Any], axis: str) -> float | None:
    summary = (measurement.get("summary") or {}).get(axis) or {}
    v = summary.get("median_seconds")
    return v if isinstance(v, (int, float)) else None


def _critical_path_top(measurement: dict[str, Any], axis: str, n: int = 3) -> list[str]:
    cp = (measurement.get("critical_path") or {}).get(axis) or {}
    nodes = cp.get("nodes") or []
    out: list[str] = []
    for node in nodes[:n]:
        name = node.get("dominant_task") or node.get("name") or "?"
        sec = node.get("duration_seconds")
        if isinstance(sec, (int, float)):
            out.append(f"{name} ({sec:.1f}s)")
        else:
            out.append(str(name))
    return out


def _write_transcript(
    *,
    ctx: DoctorContext,
    outcome: str,
    detection: str,
    measurement_path: pathlib.Path | None,
    diagnosis_path: pathlib.Path | None,
    simulation_path: pathlib.Path | None,
    ranked: list[RankedPrediction] | None,
    picked_rule: str | None,
    pick_source: str,
    fix_result_path: pathlib.Path | None,
    worktree: pathlib.Path | None,
    fix_branch: str | None,
    extra_notes: list[str] | None = None,
) -> None:
    notes = extra_notes or []
    lines: list[str] = []
    lines.append(f"# SwiftCraft demo: iOS build doctor on {ctx.project_path.name}")
    lines.append(
        f"_Run id: {ctx.run_id}, doctor.py {__version__}, "
        f"started {ctx.started_at}, finished {ctx.finished_at}_"
    )
    lines.append("")

    # ----- 1. Questionnaire ------------------------------------------------
    lines.append("## 1. Questionnaire")
    lines.append("")
    lines.append(f"- Q1 Project location: `{ctx.project_path}` (resolved from --project-path)")
    lines.append(f"- Q2 Build system: `{detection}` (auto-detected via adapters.detect_build_system)")
    lines.append(f"- Q3 Scope: `{ctx.build_types}`")
    lines.append(f"- Q4 Configuration: `{ctx.configuration}`")
    lines.append(f"- Q5 Scheme: `{ctx.scheme}`")
    lines.append(f"- Q6 Destination: `{ctx.destination}`")
    lines.append(
        f"- Q7 Constraints: repeats={ctx.repeats}, "
        f"variance-threshold-pct={ctx.variance_threshold_pct}, "
        f"CI={'set' if os.environ.get('CI') else 'unset'}"
    )
    lines.append(f"- Q8 Goal: `{ctx.goal}`")
    lines.append("")

    # ----- 2. Build-system detection --------------------------------------
    lines.append("## 2. Build-system detection")
    lines.append("")
    if detection == "xcode":
        lines.append(f"`detect_build_system({ctx.project_path})` -> `\"xcode\"`  -> v1 fence PASS")
    else:
        lines.append(
            f"`detect_build_system({ctx.project_path})` -> `\"{detection}\"`  "
            f"-> v1 fence FIRED (Tuist/Bazel end-to-end is a v1.x deferral)"
        )
    lines.append("")

    # ----- 3. Measurement -------------------------------------------------
    lines.append("## 3. Measurement")
    lines.append("")
    if measurement_path and measurement_path.exists():
        m = _load_json(measurement_path)
        clean_med = _summary_median(m, "clean")
        incr_med = _summary_median(m, "incremental")
        clean_spread = ((m.get("summary") or {}).get("clean") or {}).get("spread_percent")
        incr_spread = ((m.get("summary") or {}).get("incremental") or {}).get("spread_percent")
        lines.append(f"- Artifact: `{measurement_path}`")
        if clean_med is not None:
            sp = f"{clean_spread:.2f}%" if isinstance(clean_spread, (int, float)) else "n/a"
            lines.append(f"- Clean median: {clean_med:.2f}s (spread {sp})")
        if incr_med is not None:
            sp = f"{incr_spread:.2f}%" if isinstance(incr_spread, (int, float)) else "n/a"
            lines.append(f"- Incremental median: {incr_med:.2f}s (spread {sp})")
        for axis in ("clean", "incremental"):
            top = _critical_path_top(m, axis, 3)
            if top:
                lines.append(f"- Critical path ({axis}, top 3): " + "; ".join(top))
    else:
        lines.append("_(measurement not produced)_")
    lines.append("")

    # ----- 4. Diagnosis ---------------------------------------------------
    lines.append("## 4. Diagnosis")
    lines.append("")
    if diagnosis_path and diagnosis_path.exists():
        d = _load_json(diagnosis_path)
        findings = d.get("findings") or []
        recs = d.get("additional_recommendations") or []
        summary = d.get("summary") or {}
        by_impact = summary.get("by_impact") or {}
        lines.append(f"- Artifact: `{diagnosis_path}`")
        lines.append(f"- Findings: {len(findings)} (additional recommendations: {len(recs)})")
        if by_impact:
            parts = [f"{k}={v}" for k, v in sorted(by_impact.items())]
            lines.append(f"- By impact: {', '.join(parts)}")
    else:
        lines.append("_(diagnosis not produced)_")
    lines.append("")

    # ----- 5. Simulation top-N -------------------------------------------
    lines.append(f"## 5. Simulation top-{ctx.top_n} predictions")
    lines.append("")
    if ranked:
        lines.append("| rank | rule_id | predicted clean | predicted incremental | auto-apply |")
        lines.append("| ---- | ------- | --------------- | --------------------- | ---------- |")
        for i, r in enumerate(ranked[:ctx.top_n], start=1):
            cs = f"{r.clean_estimate:+.2f}s" if r.clean_estimate is not None else "n/a"
            isc = f"{r.incremental_estimate:+.2f}s" if r.incremental_estimate is not None else "n/a"
            aa = "YES" if r.auto_apply else "NO"
            lines.append(f"| {i} | `{r.rule_id}` | {cs} | {isc} | {aa} |")
    else:
        lines.append("_(no predictions surfaced)_")
    lines.append("")

    # ----- 6. User approval ----------------------------------------------
    lines.append("## 6. User approval")
    lines.append("")
    if picked_rule:
        lines.append(f"- choice: `{picked_rule}`")
        lines.append(f"- source: {pick_source}")
    else:
        lines.append(f"- choice: (none) — source: {pick_source}")
    lines.append("")

    # ----- 7. Fix --------------------------------------------------------
    lines.append("## 7. Fix")
    lines.append("")
    if worktree:
        lines.append(f"- Worktree: `{worktree}`")
    if fix_branch:
        lines.append(f"- Branch: `{fix_branch}`")
    if fix_result_path and fix_result_path.exists():
        fr = _load_json(fix_result_path)
        applied = fr.get("applied_fix") or {}
        kind = applied.get("kind", "n/a")
        files = applied.get("files_modified") or []
        sha_before = applied.get("git_sha_before", "n/a")
        sha_after = applied.get("git_sha_after", "n/a")
        lines.append(f"- Apply kind: `{kind}`")
        lines.append(f"- Files modified ({len(files)}): " + (", ".join(f"`{f}`" for f in files) or "_none_"))
        lines.append(f"- Git SHA before/after: `{sha_before[:10]}` -> `{sha_after[:10]}`")
        lines.append(f"- Artifact: `{fix_result_path}`")
    else:
        lines.append("_(fix-result not produced)_")
    lines.append("")

    # ----- 8. Result -----------------------------------------------------
    lines.append("## 8. Result")
    lines.append("")
    if fix_result_path and fix_result_path.exists():
        fr = _load_json(fix_result_path)
        clean_p = _read_predicted(fr, "clean")
        clean_a = _read_actual(fr, "clean")
        incr_p = _read_predicted(fr, "incremental")
        incr_a = _read_actual(fr, "incremental")
        lines.append(
            f"- predicted clean: "
            f"{clean_p:+.3f}s" if isinstance(clean_p, (int, float)) else "- predicted clean: n/a"
        )
        lines.append(
            f"- actual clean:    "
            f"{clean_a:+.3f}s" if isinstance(clean_a, (int, float)) else "- actual clean:    n/a"
        )
        lines.append(f"- predicted-vs-actual (clean): {_gap_line(clean_p, clean_a)}")
        lines.append(
            f"- predicted incremental: "
            f"{incr_p:+.3f}s" if isinstance(incr_p, (int, float)) else "- predicted incremental: n/a"
        )
        lines.append(
            f"- actual incremental:    "
            f"{incr_a:+.3f}s" if isinstance(incr_a, (int, float)) else "- actual incremental:    n/a"
        )
        lines.append(f"- predicted-vs-actual (incremental): {_gap_line(incr_p, incr_a)}")
        lines.append("")
        lines.append(f"- **outcome (verbatim from fix-result.json): `{fr.get('outcome', 'n/a')}`**")
        reason = fr.get("outcome_reason")
        if reason:
            lines.append(f"- outcome_reason: {reason}")
        lines.append("")
        lines.append(f"- doctor outcome: `{outcome}`")
    else:
        lines.append(f"- doctor outcome: `{outcome}` _(no fix-result available)_")
    lines.append("")

    # ----- Wall-clock delta ----------------------------------------------
    lines.append("## Wall-clock delta")
    lines.append("")
    lines.append(f"- doctor started: {ctx.started_at}")
    lines.append(f"- doctor finished: {ctx.finished_at}")
    lines.append(f"- run id: {ctx.run_id}")
    if notes:
        lines.append("")
        lines.append("### Notes")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")

    ctx.transcript_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.transcript_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[doctor.py] transcript written: {ctx.transcript_path}")


def _write_run_metadata(
    *,
    ctx: DoctorContext,
    outcome: str,
    extras: dict[str, Any] | None = None,
) -> None:
    extras = extras or {}
    payload = {
        "tool": {"name": "ios-build-doctor", "version": __version__},
        "run_id": ctx.run_id,
        "started_at": ctx.started_at,
        "finished_at": ctx.finished_at,
        "outcome": outcome,
        "args": {
            "project_path": str(ctx.project_path),
            "scheme": ctx.scheme,
            "configuration": ctx.configuration,
            "destination": ctx.destination,
            "build_types": ctx.build_types,
            "repeats": ctx.repeats,
            "touch_file": str(ctx.touch_file) if ctx.touch_file else None,
            "output_dir": str(ctx.output_dir),
            "top_n": ctx.top_n,
            "worktree_base": str(ctx.worktree_base),
            "branch_prefix": ctx.branch_prefix,
            "variance_threshold_pct": ctx.variance_threshold_pct,
            "auto_approve_fix": ctx.auto_approve_fix,
            "allow_manual": ctx.allow_manual,
            "rule_id": ctx.rule_id,
            "non_interactive": ctx.non_interactive,
            "transcript_path": str(ctx.transcript_path),
            "keep_worktree": ctx.keep_worktree,
            "no_verify_commits": ctx.no_verify_commits,
            "goal": ctx.goal,
            "worktree_seed_ref": ctx.worktree_seed_ref,
        },
        **extras,
    }
    target = ctx.output_dir / "run.json"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# main() — sequencing
# ---------------------------------------------------------------------------


def _exit_code_for(outcome: str) -> int:
    if outcome == "success":
        return 0
    if outcome.startswith("refused-"):
        return 0
    if outcome.startswith("info:"):
        return 0
    if outcome == "abort:non-xcode-v1-fence":
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    ctx = _resolve_questionnaire(args)
    ctx.run_id = _utc_stamp()
    ctx.started_at = ctx.run_id

    measurement_path: pathlib.Path | None = None
    diagnosis_path: pathlib.Path | None = None
    simulation_path: pathlib.Path | None = None
    fix_result_path: pathlib.Path | None = None
    worktree: pathlib.Path | None = None
    fix_branch: str | None = None
    ranked: list[RankedPrediction] | None = None
    picked_rule: str | None = None
    pick_source: str = "(unset)"
    notes: list[str] = []

    def _finish(outcome: str) -> int:
        ctx.finished_at = _utc_stamp()
        _write_transcript(
            ctx=ctx,
            outcome=outcome,
            detection=ctx.build_system or "(unset)",
            measurement_path=measurement_path,
            diagnosis_path=diagnosis_path,
            simulation_path=simulation_path,
            ranked=ranked,
            picked_rule=picked_rule,
            pick_source=pick_source,
            fix_result_path=fix_result_path,
            worktree=worktree,
            fix_branch=fix_branch,
            extra_notes=notes,
        )
        _write_run_metadata(ctx=ctx, outcome=outcome)
        return _exit_code_for(outcome)

    # ----- Step 2: build-system detection / v1 fence -----------------------
    detection = _detect_or_confirm_build_system(ctx)
    if detection != "xcode":
        notes.append(
            f"v1 fence fired: detect_build_system returned {detection!r}. "
            f"Tuist/Bazel end-to-end is deferred to v1.x."
        )
        return _finish("abort:non-xcode-v1-fence")

    # ----- Step 3: measure -------------------------------------------------
    rc, measurement_path = _run_measure(ctx)
    if rc != 0:
        notes.append(f"benchmark.py exited rc={rc}")
        return _finish("abort:measure-failed")

    if ctx.goal == "baseline":
        notes.append("goal=baseline; stopping after measure (info:baseline-only).")
        return _finish("info:baseline-only")

    # ----- Step 4: diagnose ------------------------------------------------
    rc, diagnosis_path = _run_diagnose(ctx, measurement_path)
    if rc != 0:
        notes.append(f"diagnose.py exited rc={rc}")
        return _finish("abort:diagnose-failed")

    # ----- Step 5: simulate ------------------------------------------------
    rc, simulation_path = _run_simulate(ctx, diagnosis_path, measurement_path)
    if rc != 0:
        notes.append(f"simulate.py exited rc={rc}")
        return _finish("abort:simulate-failed")

    simulation = _load_json(simulation_path)
    ranked = _rank_predictions(simulation)

    # ----- Step 6/7: top-N + approval -------------------------------------
    picked_rule, picked_entry, pick_source = _present_top_n_and_prompt(ctx, ranked)
    if picked_rule is None:
        if pick_source == "no-actionable":
            return _finish("abort:no-actionable")
        return _finish("info:user-declined")

    # ----- Step 8: worktree ------------------------------------------------
    git_root = _fix_module._find_git_root(ctx.project_path) or ctx.project_path
    try:
        worktree = _setup_worktree(ctx, git_root, branch_seed=ctx.worktree_seed_ref)
    except Exception as exc:  # noqa: BLE001
        notes.append(f"worktree setup failed: {exc}")
        return _finish("abort:worktree-failed")

    # Translate the user's --project-path (relative to git_root) into the
    # equivalent path inside the throwaway worktree so fix.py's
    # --project-root points at the *.xcodeproj directory, not the worktree
    # root. For REDACTED, project_path = ~/REDACTED/REDACTED,
    # git_root = ~/REDACTED, rel = REDACTED,
    # worktree = /tmp/REDACTED-doctor-<ts>, worktree_project = /tmp/.../REDACTED.
    try:
        rel = ctx.project_path.relative_to(git_root)
    except ValueError:
        rel = pathlib.Path(".")
    worktree_project = (worktree / rel).resolve() if str(rel) not in ("", ".") else worktree

    # Phase A D.6 — translate ctx.touch_file the same way so fix.py's
    # post-fix mtime touch hits the throwaway worktree's working copy,
    # not the primary checkout. F4's clean-axis gate is unaffected by
    # the prior Phase A confound, but the incremental axis was; this
    # patch makes the incremental measurement well-defined.
    worktree_touch_file: pathlib.Path | None
    if ctx.touch_file is not None:
        try:
            rel_touch = ctx.touch_file.relative_to(git_root)
            worktree_touch_file = (worktree / rel_touch).resolve()
        except ValueError:
            worktree_touch_file = ctx.touch_file
    else:
        worktree_touch_file = None

    # ----- Step 9: fix -----------------------------------------------------
    try:
        rc, fix_result_path = _run_fix(
            ctx=ctx,
            rule_id=picked_rule,
            diagnosis_artifact=diagnosis_path,
            simulation_artifact=simulation_path,
            measurement_pre=measurement_path,
            worktree=worktree_project,
            touch_file_for_fix=worktree_touch_file,
        )
    finally:
        # Always attempt teardown unless --keep-worktree.
        try:
            _teardown_worktree(ctx, git_root, worktree)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"worktree teardown failed: {exc}")

    if not fix_result_path.exists():
        notes.append(f"fix.py exited rc={rc} but no fix-result.json was produced.")
        return _finish("abort:fix-failed")

    fix_result = _load_json(fix_result_path)
    fix_branch = (fix_result.get("inputs") or {}).get("branch")

    fix_outcome = fix_result.get("outcome", "abort:fix-failed")
    if fix_outcome == "success":
        return _finish("success")
    if isinstance(fix_outcome, str) and fix_outcome.startswith("refused-"):
        return _finish(fix_outcome)
    if rc != 0:
        notes.append(f"fix.py exited rc={rc} with outcome={fix_outcome!r}.")
        return _finish("abort:fix-failed")
    notes.append(f"unrecognized fix outcome={fix_outcome!r}")
    return _finish("abort:fix-failed")


if __name__ == "__main__":
    sys.exit(main())
