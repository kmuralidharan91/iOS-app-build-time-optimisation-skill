---
name: ios-build-fix
description: Apply a single approved finding from ios-build-diagnose to an Xcode project on a throwaway branch, re-measure with ios-build-measure, report wall-clock delta, and refuse to claim success when the delta is null, regressive, or within variance noise. Single-fix-at-a-time; atomic git-aware (creates a branch named ios-build-fix/<rule-slug>-<timestamp>); predicted-vs-actual logged for every run. Auto-applicable v1 surface is F1 (random-sleep), F3 (sandbox+fuse via xcconfig), F4 (compilation-cache-disabled), F9 (eager-linking-disabled, designed null-delta refusal-path test); F5/F6/F7 emit a manual recipe and refuse to mutate the tree without --allow-manual. Use when the user wants a fix applied + verified end-to-end, not just predicted; refuse politely otherwise.
disable-model-invocation: true
---

# `ios-build-fix`

> **Side-effects warning.** This skill modifies the user's Xcode project (xcconfig edits, script-phase line deletions, package-pin changes). It must only run when the user has **explicitly approved** a specific finding from `ios-build-diagnose` — typically via `ios-build-doctor`'s approval gate, or via a direct user invocation of `/ios-build-fix <rule-id>`. The Claude Code frontmatter sets `disable-model-invocation: true` so Claude will not invoke the skill autonomously. **If the agent runtime you are using does not honour `disable-model-invocation` (e.g. some Codex / Copilot / Windsurf modes), do NOT let the agent call this skill on its own — invoke it yourself after reviewing the doctor's recommendation.** All changes go to a throwaway git branch first; the fixer re-measures and refuses to claim success on null/regressive delta.

Applies one diagnose finding (or a group of same-rule findings) to a real Xcode project, re-measures wall-clock via `ios-build-measure`, and emits a `fix-result.json` artifact whose `outcome` field carries the verdict honestly: `success`, `refused-null`, `refused-regressive`, `refused-noise`, `refused-apply-error`, or `refused-benchmark-error`. The fixer never claims a win when the post-fix delta is null, regressive, or within the variance threshold — that refusal is the credibility hinge for the doctor-loop demo.

## When to use

Reach for this skill when the user wants the **apply** answer:

- "Apply this fix and tell me whether it actually helped."
- "I approved this finding from diagnose; go fix it on a throwaway branch."
- "Run the fix end-to-end and refuse if the win is noise."

If the user wants the predicted Δ (no apply), use [`ios-build-simulate`](../ios-build-simulate/SKILL.md). If they want the audit, use [`ios-build-diagnose`](../ios-build-diagnose/SKILL.md). If they want the full questionnaire → measure → diagnose → simulate → fix → re-measure loop in one transcript, use `ios-build-doctor`; this skill is the apply step.

## Inputs

| Argument | Required | Default | Notes |
| --- | --- | --- | --- |
| `--diagnosis-artifact PATH` | yes | — | `diagnosis.json` (`ios-build-diagnose` output). |
| `--simulation-artifact PATH` | no | — | `simulation.json` (`ios-build-simulate` output). When supplied, the predicted Δ is recorded in the fix-result and used to compute `within_predicted_band`. |
| `--rule-id RULE` | yes | — | One rule_id from the diagnosis. All findings sharing that rule_id are closed by a single atomic edit. |
| `--project-root DIR` | yes | — | Throwaway worktree of the project. The fixer creates a branch here; never edit the user's working tree directly. |
| `--branch-prefix PREFIX` | no | `ios-build-fix` | Branch is named `<prefix>/<rule-slug>-<UTC-timestamp>`. |
| `--output-dir DIR` | yes | — | Where `fix-result.json` + per-axis `measurement-pre/`, `measurement-post/` are written. |
| `--auto-approve` | no | off | Skip the `[y/N]` preview prompt. Required for non-interactive use (smoke runs + doctor-loop). |
| `--allow-refusal` | no | off | Exit `0` even when the outcome is `refused-*`. Used for the F9 designed null-delta refusal-path test. |
| `--allow-manual` | no | off | Allow informational rule_ids (F5/F6/F7) to no-op-apply for record-keeping. Without it, the orchestrator refuses these rules with a clear error. |
| `--variance-threshold-pct N` | no | `10.0` | Percent-of-baseline floor below which `|delta|` is classified as noise. Matches the measure-gate spec. |
| `--repeats N` | no | `3` | Forwarded to `benchmark.py`. |
| `--build-types LIST` | no | `incremental` | Comma-separated; `clean` and/or `incremental`. |
| `--touch-file PATH` | when `incremental` is requested | — | Forwarded to `benchmark.py`. |
| `--scheme NAME` | no | `Debug` | Forwarded to `benchmark.py`. |
| `--configuration NAME` | no | `Debug` | Forwarded to `benchmark.py`. |
| `--destination STRING` | no | `generic/platform=iOS Simulator` | Forwarded to `benchmark.py`. |
| `--reuse-measurement-pre PATH` | no | — | Reuse a pre-existing `measurement.json` as the pre-fix baseline (skips the first benchmark run; saves ~30 min on incremental, ~25 min on clean). Caller is responsible for ensuring it was taken at the same git SHA on the same machine. |

## Workflow

1. **Resolve the fixer** by `rule_id` via `scripts/fixers/registry.py::resolve()`. Unregistered rules error out with the registry list. Informational rules (F5/F6/F7) require `--allow-manual`.
2. **Approval gate**. Print the per-rule preview (file paths + diff outline + branch name + predicted Δ when simulation_artifact is supplied). Without `--auto-approve`, prompt `[y/N]`; refuse on anything but `y` / `yes`.
3. **Branch create**. `git checkout -b <branch>` in `--project-root`. If the branch already exists (re-runs), check it out without discarding work.
4. **Pre-fix measurement**. Either invoke `scripts/benchmark.py` with the forwarded flags into `--output-dir/measurement-pre/`, or — when `--reuse-measurement-pre` is supplied — load the path. On benchmark failure: `git reset --hard HEAD~0`, emit `outcome=refused-benchmark-error`.
5. **Apply**. Call `fixer.apply(findings, ctx)` which mutates files and commits atomically (in the submodule first, then the parent worktree). On `ApplyError`, `git reset --hard <sha_before>`, emit `outcome=refused-apply-error`.
6. **Post-fix measurement**. Same as step 4, into `--output-dir/measurement-post/`.
7. **Compute deltas + outcome**. Per-axis (clean / incremental):
   - `delta_seconds = post_median − pre_median` (negative = improvement).
   - `exceeds_variance = |delta| > variance_threshold_pct × max(pre_median, post_median) / 100`.
   - `within_predicted_band` (when simulation supplied): `|delta − predicted| ≤ 0.5 × |predicted|` per the simulate ±50 % rule.
   - **Outcome decision**:
     - both axes `delta is None` ⇒ `refused-null`.
     - any axis `delta < 0` AND `exceeds_variance` ⇒ `success` (single-axis win is enough).
     - every measured axis `delta ≥ 0` ⇒ `refused-regressive`.
     - else (every measured axis under variance noise) ⇒ `refused-noise`.
8. **Persist** `fix-result.json` per [`schemas/fix-result.schema.json`](../../schemas/fix-result.schema.json). jsonschema-validate.
9. **Print summary** — predicted Δ, actual Δ, outcome, outcome_reason. Exit `0` on `success` (or any `refused-*` with `--allow-refusal`); `1` otherwise.

## Outputs

A single JSON artifact at `<output-dir>/fix-result.json` plus per-axis sub-directories:

```
<output-dir>/
  fix-result.json            # the artifact (jsonschema-valid)
  measurement-pre/           # benchmark.py output (or empty when --reuse-measurement-pre)
    measurement.json
    runs-{clean,incremental}/...
  measurement-post/          # always written when apply succeeded
    measurement.json
    runs-{clean,incremental}/...
```

Top-level fields of `fix-result.json` (full schema in `schemas/fix-result.schema.json`):

- `schema_version`: `"1.0.0"`.
- `tool`: `{name: "ios-build-fix", version}`.
- `generated_at`, `inputs`, `target` (rule_id + family + source_finding_indices + predicted Δ).
- `applied_fix`: `{kind, files_modified[], git_sha_before, git_sha_after, submodule_changes[]}`.
- `measurement_pre` / `measurement_post`: each is a reference (path + git_sha + schema_version + summary medians / spreads) to the underlying `measurement.json`.
- `actual_delta`: per-axis `{delta_seconds, baseline_median_seconds, post_median_seconds, spread_pre_percent, spread_post_percent, exceeds_variance, within_predicted_band}`.
- `variance_threshold_percent`.
- `outcome`: enum (see refusal taxonomy above).
- `outcome_reason`: one-sentence explanation.
- `tuning_data_point`: required string mirroring the simulate convention — feeds back into the next iteration of the simulate predictor.
- `notes[]`.

## Failure modes

- **VPN down / network failure**. The fixer calls `xcodebuild` indirectly via `benchmark.py`; SPM resolution against gitlab/github mirrors may fail. The fixer reports `refused-benchmark-error` with the exit code; tree is reset.
- **Apply error**. The per-rule fixer raises `ApplyError` (e.g. F1 cannot find a regex-matching `sleep $[ ... ]s` line). Tree is reset; branch left in place for inspection. `outcome=refused-apply-error`.
- **Benchmark crash**. `xcodebuild` non-zero exit during pre or post measurement. `outcome=refused-benchmark-error`; tree reset.
- **Variance noise**. Real machines have spread; small predicted wins (e.g. F1's -5.5 s on a 51.8 s baseline ≈ 10.6 %) sit on the variance threshold and may classify as `refused-noise`. The fixer says so explicitly — that is the *intended* credibility behaviour. Re-running with `--repeats 5` may reduce variance enough to clear the bar.
- **Null delta on a designed null-delta rule (F9)**. Expected. Run with `--allow-refusal` to exit `0` while still recording the refusal. F9's role is to prove the fixer refuses honestly.

## Auto-applicable surface (v1)

| rule_id | auto_apply | Edit kind | Fix target |
| --- | :---: | --- | --- |
| `script-phase/random-sleep` (F1) | ✓ | `delete-line` | The first matching `sleep $[ ... ]s` line in any reachable build-phase script. |
| `script-phase/missing-output-declarations` (F3) | ✓ | `edit-xcconfig` | `Configurations/Project/Local/local-debug.xcconfig` — appends `ENABLE_USER_SCRIPT_SANDBOXING = YES` + `FUSE_BUILD_SCRIPT_PHASES = YES`. |
| `build-setting/compilation-cache-disabled` (F4) | ✓ | `edit-xcconfig` | Same xcconfig — appends `COMPILATION_CACHE_ENABLE_CACHING = YES`. Warm-cache test required. |
| `build-setting/eager-linking-disabled` (F9) | ✓ | `edit-xcconfig` | Same xcconfig — appends `EAGER_LINKING = YES`. Designed null-delta refusal-path test. |
| `asset-catalog/incremental-recompile` (F5) | ✗ | `no-op` | Manual recipe in preview. |
| `spm/swift-syntax-not-prebuilt` (F6) | ✗ | `no-op` | "Upgrade to Xcode 26" — manual recipe. |
| `spm/oversized-module` (F7) | ✗ | `no-op` | Module-split is architectural; recipe only. |

The xcconfig lookup is in `scripts/fixers/{script_phase,build_setting}.py::_*xcconfig*` and extends to other layouts by adding candidate paths. TODO(public-cite: NetNewsWire) confirm or extend the candidate paths for the public-cite project layout.

## References

- [`schemas/fix-result.schema.json`](../../schemas/fix-result.schema.json) — output artifact contract.
- [`scripts/fix.py`](../../scripts/fix.py) — orchestrator; argparse CLI; refusal logic.
- [`scripts/fixers/`](../../scripts/fixers/) — per-rule fixers; `registry.py` is the dispatch table.
- [`scripts/benchmark.py`](../../scripts/benchmark.py) — pre/post measurement.
- [`references/defaults.md`](../../references/defaults.md) — variance-threshold tuning data + per-rule prediction tuning points.
- [`docs/PLAN.md`](../../docs/PLAN.md) "Verification — Fix gate" — fix-step effectiveness gate criteria.
