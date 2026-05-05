---
name: ios-build-doctor
description: Orchestrate iOS build optimisation end-to-end — questionnaire, build-system detection, measure, diagnose, simulate, top-N approval prompt, fix-on-throwaway-worktree, re-measure, and a single transcript artifact suitable for a SwiftCraft-style demo. Refuses politely when the build system is not Xcode (v1 fence — emits `outcome=abort:non-xcode-v1-fence` and writes a transcript that records the fence firing) or when fix.py can't deliver (refused-* outcomes are surfaced verbatim, not masked). Manual-only rules (F5/F6/F7) are always routed through fix.py with --allow-manual so the no-op fix-result is captured for tuning data, never short-circuited to a recipe-only path. Use when the user wants the full doctor-loop in one transcript, not just one step.
---

# `ios-build-doctor`

Drives the four prior skills end-to-end and produces a single transcript artifact: questionnaire → adapter detection → `ios-build-measure` → `ios-build-diagnose` → `ios-build-simulate` → top-N approval prompt → `ios-build-fix` on a throwaway worktree → re-measure → predicted-vs-actual line. The doctor's only original work is the **glue**: rule-id ranking, the user-approval gate, worktree management, and the transcript writer. Every other step delegates to a sibling CLI by subprocess, so the doctor cannot accidentally regress the production-ready `scripts/fix.py`.

## When to use

Reach for this skill when the user wants the **full loop** answer:

- "Run the doctor on this project."
- "Give me one transcript that shows everything — measure, find, fix, re-measure."
- "I want the SwiftCraft-style demo on REDACTED develop."

If the user wants just baselines, use [`ios-build-measure`](../ios-build-measure/SKILL.md). For just the audit, use [`ios-build-diagnose`](../ios-build-diagnose/SKILL.md). For predicted Δ, use [`ios-build-simulate`](../ios-build-simulate/SKILL.md). For one approved fix end-to-end (no questionnaire / no top-N), use [`ios-build-fix`](../ios-build-fix/SKILL.md).

## Inputs

| Argument | Required | Default | Notes |
| --- | --- | --- | --- |
| `--project-path PATH` | yes | — | Project root containing `*.xcodeproj` or `*.xcworkspace`. |
| `--scheme NAME` | yes | — | Forwarded to all sub-CLIs. |
| `--configuration NAME` | no | `Debug` | Forwarded. |
| `--destination STRING` | no | `generic/platform=iOS Simulator` | Forwarded. |
| `--touch-file PATH` | when `incremental` is in `--build-types` | — | Forwarded to measure + fix. |
| `--build-types LIST` | no | `clean,incremental` | Comma list. |
| `--repeats N` | no | `3` | Forwarded. |
| `--output-dir DIR` | yes | — | Run-rooted; e.g. `docs/smoke/5/run-001/`. |
| `--top-n N` | no | `3` | How many ranked predictions are surfaced for approval. |
| `--worktree-base DIR` | no | `/tmp` | Parent of the throwaway worktree (`/tmp/REDACTED-doctor-<UTC-ts>/`). |
| `--branch-prefix PREFIX` | no | `gh-skill-test` | Forwarded to fix.py. |
| `--variance-threshold-pct N` | no | `10.0` | Forwarded to measure + fix. |
| `--auto-approve-fix` | no | off | Forwarded as `--auto-approve` to fix.py. Doctor's own pick gate is unaffected. |
| `--allow-manual` | no | off | Always forwarded to fix.py — manual rules (F5/F6/F7) emit `refused-null` fix-result for tuning data instead of being short-circuited. |
| `--rule-id RULE` | no | — | Skip the top-N approval prompt; pre-pick this rule. Used by smoke runs. |
| `--reuse-measurement-artifact PATH` | no | — | Skip the pre-measure subprocess and reuse a pre-existing `measurement.json`. Useful for retrying a failed mid-run smoke without redoing the ~25-min benchmark phase. Caller asserts SHA / project-path equivalence. |
| `--reuse-diagnosis-artifact PATH` | no | — | Skip the diagnose subprocess and reuse a pre-existing `diagnosis.json`. Caller asserts SHA-equivalence with `--worktree-seed-ref` (e.g. REDACTED smokes pinned at `REDACTED` reuse `docs/smoke/2/diagnosis.json`). Workaround for the Phase A `xcode_adapter` stub gap; proper backfill is Phase A polish scope. |
| `--reuse-simulation-artifact PATH` | no | — | Skip the simulate subprocess and reuse a pre-existing `simulation.json`. Same SHA-equivalence caveat. |
| `--non-interactive` | no | off | Refuse to prompt; require `--rule-id`. |
| `--transcript-path PATH` | no | `<output-dir>/swiftcraft-loop.md` | Override transcript location. |
| `--keep-worktree` | no | off | Skip `git worktree remove` for post-mortem inspection. |
| `--no-verify-commits` | no | off | Forwarded to fix.py for projects whose hooks gate on branch-name patterns. |
| `--goal {baseline,find,apply}` | no | `find` | Q8: `baseline` stops after measure, `find` is the full loop, `apply` requires `--rule-id` and skips the top-N gate. |

## Workflow

1. **Resolve the questionnaire** via `_resolve_questionnaire` (CLI flags + auto-detect; see [The questionnaire](#the-questionnaire) below). Produces a `DoctorContext` carrying every answer + path.
2. **Build-system detection**. `adapters.detect_build_system(project_path)` returns `"xcode" | "tuist" | "bazel"`. **v1 fence**: anything but `"xcode"` writes a transcript with `outcome=abort:non-xcode-v1-fence` and exits 0 — the fence firing is itself a successful doctor action.
3. **Measure**. Subprocess `scripts/benchmark.py` into `<output-dir>/measurement/`. Non-zero exit → `outcome=abort:measure-failed`.
4. **Goal=baseline short-circuit**. If `--goal=baseline`, write the transcript with `outcome=info:baseline-only` and exit 0 here.
5. **Diagnose**. Subprocess `scripts/diagnose.py` into `<output-dir>/diagnosis/`. Non-zero → `abort:diagnose-failed`.
6. **Simulate**. Subprocess `scripts/simulate.py` into `<output-dir>/simulation/`. Non-zero → `abort:simulate-failed`.
7. **Rank predictions**. Auto-applicable rules first (per `scripts/fixers/registry.py::build_registry()`), then by `max(|clean|, |incremental|)` descending. Rules with both axes at zero are hidden — exception: F9 (`build-setting/eager-linking-disabled`) stays visible because it is the designed null-delta refusal-path test.
8. **Top-N + approval**. Print the top-N table; prompt `Enter choice [1..N or s]:`. `--rule-id` skips the prompt. Pick `s` (skip) → `outcome=info:user-declined`. Empty top-N → `abort:no-actionable`.
9. **Worktree setup**. `git worktree add --detach /tmp/REDACTED-doctor-<UTC-ts>/ develop` from the user's primary checkout. Then `git submodule update --init --recursive` inside the worktree (best-effort; tolerated rc≠0 for projects without submodules). On worktree failure → `abort:worktree-failed`.
10. **Fix**. Subprocess `scripts/fix.py` with `--reuse-measurement-pre` pointing at the step-3 measurement (saves the duplicate baseline benchmark) and **always** with `--auto-approve --allow-refusal --allow-manual`. Doctor already got the user's pick at step 8, so fix.py's own `[y/N]` prompt is unwanted; refusal is honest-PASS for the demo; and manual rules are routed through fix.py with `--allow-manual` to capture a `refused-null` fix-result for tuning data.
11. **Read fix-result.json**, propagate the outcome verbatim, and compute the predicted-vs-actual line:
    - `gap = |predicted - actual|`
    - `error_pct = 100 × gap / |predicted|` (or absolute tolerance when `predicted = 0`).
12. **Worktree teardown**. `git worktree remove --force <path>` unless `--keep-worktree`. Fallback: `rmtree` + `git worktree prune`.
13. **Write transcript**. Eight-section markdown at `<output-dir>/swiftcraft-loop.md` (override with `--transcript-path`). The transcript is auto-generated; Claude may post-edit lightly before committing the canonical demo copy at `docs/demo/swiftcraft-loop-<date>.md`.

## The questionnaire

Eight numbered questions; answers populate `DoctorContext`. Auto-detect first; ask only when ambiguous. Source of truth: `docs/PLAN.md` § "Questionnaire — pre-execution UX".

| # | Question | Auto-detect | Ask only if | Populates |
| --- | --- | --- | --- | --- |
| 1 | Project location | `--project-path` flag → `pwd` fallback | flag missing AND `pwd` lacks build-system signals | `project_path` |
| 2 | Build system | `adapters.detect_build_system(project_path)` | detector raises ambiguity | `build_system` (v1 fence: must be `"xcode"`) |
| 3 | Scope (clean/incr/both) | none | always | `build_types` |
| 4 | Configuration | none, default `Debug` | always | `configuration` |
| 5 | Scheme/Target | `xcodebuild -list` if exactly one | scheme list ≠ 1 OR `--scheme` missing | `scheme` |
| 6 | Destination | none, default `generic/platform=iOS Simulator` | always (prefilled) | `destination` |
| 7 | Constraints (time/CI) | infer CI from `os.environ.get("CI")` | always | `repeats`, `touch_file` |
| 8 | Goal (baseline/find/apply) | none, default `find` | always | `goal` |

## Top-N ranking

The doctor's top-N table follows two rules: **auto-applicable rules first**, then **descending magnitude** by `max(|clean|, |incremental|)`. On REDACTED develop, that places F4 (`build-setting/compilation-cache-disabled`, predicted -183.5s clean) at rank 1 reliably; F3 and F1 (or F9 with its designed null-delta) typically fill ranks 2–3.

Each row prints rule_id, predicted Δ on each axis, the auto-apply YES/NO badge (from the registry), confidence, and (when present) the diagnose finding's title. The user enters `1..N` or `s`.

## Manual-only rules (F5/F6/F7)

These are informational rules — the v1 fixer for each is a no-op that produces a manual recipe in `applied_fix.notes`. Per the Phase A plan decision, the doctor **always** forwards `--allow-manual` to fix.py so:

- The user can still pick F5/F6/F7 from the top-N list.
- fix.py applies the no-op, runs the post-fix benchmark, and emits `outcome=refused-null` (per `schemas/fix-result.schema.json` — null delta on the relevant axis when no real edit was made).
- The recipe and tuning-data point land in `fix-result.json.notes` and `fix-result.json.tuning_data_point`, joining the same artifact stream as auto-applicable rules.

This means manual rules are never silently skipped or routed to a parallel "recipe-only" code path — they go through the same orchestrator with the same artifact shape, which makes cross-rule analysis (e.g. when re-tuning predictors) trivially uniform.

## Outputs

```
<output-dir>/
  run.json                         # doctor metadata: run_id, args, started/finished, outcome
  swiftcraft-loop.md               # the transcript artifact (override with --transcript-path)
  measurement/
    measurement.json
    runs-{clean,incremental}/...
  diagnosis/
    diagnosis.json
  simulation/
    simulation.json
  fix-<rule-slug>/                 # only when a rule was picked + applied
    fix-result.json
    measurement-post/measurement.json
    measurement-post/runs-{...}/
```

The transcript has eight sections matching the effectiveness-gate's eight bullets:

1. Questionnaire (Q1..Q8 with auto-detected vs user-supplied annotation)
2. Build-system detection (v1 fence verdict)
3. Measurement (clean + incremental medians, spread%, top-3 critical_path)
4. Diagnosis (finding count by impact, additional_recommendations count)
5. Simulation top-N predictions (rank | rule_id | clean Δ | incremental Δ | auto-apply)
6. User approval (chosen rule_id + pick source: prompt vs `--rule-id` flag)
7. Fix (worktree path, branch, applied_fix.kind + files_modified, git SHAs)
8. Result (predicted-vs-actual line per axis + outcome verbatim from `fix-result.json` + outcome_reason)

## Failure modes

| Trigger | Outcome | Exit |
| --- | --- | --- |
| Tuist/Bazel detected | `abort:non-xcode-v1-fence` | 0 (fence firing is honest success) |
| benchmark.py exit ≠ 0 | `abort:measure-failed` | 1 |
| diagnose.py exit ≠ 0 | `abort:diagnose-failed` | 1 |
| simulate.py exit ≠ 0 | `abort:simulate-failed` | 1 |
| Empty top-N (no actionable rule) | `abort:no-actionable` | 1 |
| User picks `s` | `info:user-declined` | 0 |
| `--goal=baseline` short-circuit | `info:baseline-only` | 0 |
| `git worktree add` failure | `abort:worktree-failed` | 1 |
| fix.py outcome=`success` | `success` | 0 |
| fix.py outcome=`refused-*` | `<refused-*>` (verbatim) | 0 |
| fix.py exit ≠ 0 / no fix-result.json | `abort:fix-failed` | 1 |

The `abort:` and `info:` prefixes are doctor-only; they never collide with fix.py's `success`/`refused-*` enum (`schemas/fix-result.schema.json:156-166`).

## Self-imposed rules

- **Doctor never edits the user's working tree.** All apply happens in `git worktree add /tmp/REDACTED-doctor-<UTC-ts>/`. The user's primary checkout's HEAD is read-only to the doctor.
- **Doctor never claims a win that fix.py refused.** The transcript reproduces `fix-result.json.outcome` verbatim. If fix.py says `refused-regressive`, the transcript says `refused-regressive` — never "almost" or "borderline".
- **Doctor's top-N prompt is the only user-visible decision point.** fix.py is always run with `--auto-approve` to avoid double-prompts in the transcript.
- **Doctor always forwards `--allow-manual`** so manual rules emit `refused-null` fix-results in the same artifact stream as auto-applicable rules.

## References

- [`scripts/doctor.py`](../../scripts/doctor.py) — orchestrator; argparse CLI; the eight workflow steps.
- [`scripts/benchmark.py`](../../scripts/benchmark.py) — measure CLI (Phase A).
- [`scripts/diagnose.py`](../../scripts/diagnose.py) — diagnose CLI (Phase A).
- [`scripts/simulate.py`](../../scripts/simulate.py) — simulate CLI (Phase A).
- [`scripts/fix.py`](../../scripts/fix.py) — fix CLI (Phase A); doctor reuses `_find_git_root` via import.
- [`scripts/fixers/registry.py`](../../scripts/fixers/registry.py) — auto_apply flag per rule_id, used to filter the top-N.
- [`schemas/fix-result.schema.json`](../../schemas/fix-result.schema.json) — outcome enum that doctor's superset extends.
- [`docs/PLAN.md`](../../docs/PLAN.md) "Questionnaire — pre-execution UX" + "Per-chat protocol" — the contract this skill executes.
- [`docs/demo/swiftcraft-loop-2026-05-05.md`](../../docs/demo/swiftcraft-loop-2026-05-05.md) — canonical SwiftCraft demo transcript (produced by `--rule-id build-setting/compilation-cache-disabled` against REDACTED develop @ `REDACTED`).
