---
name: ios-build-simulate
description: Predict Δ wall-clock per diagnose rule (clean and incremental separately) before applying any fix. Reads a diagnosis.json (and optionally a measurement.json for project-context-aware scaling), aggregates findings sharing a rule_id into one prediction, and emits a JSON artifact with a tuning_data_point on every Prediction (the project + run that motivated the predicted Δ). Use when the user asks "what will I save if I fix X?", wants to compare candidate fixes by predicted impact, or needs to surface a trade-off (e.g. F4 saves clean but costs incremental). Recommend-first; this skill never edits source files and never invokes xcodebuild. Output is always labelled "predicted Δ", never "measured".
---

# `ios-build-simulate`

Reads a `diagnosis.json` and emits a ranked JSON artifact of predicted Δ wall-clock per rule. Each prediction aggregates the diagnosis findings sharing a rule_id into one `RulePrediction` so the user sees one prediction per fix, not N copies. Clean and incremental axes are surfaced separately so trade-offs (e.g. `COMPILATION_CACHE_ENABLE_CACHING` saves clean but costs incremental) cannot hide behind a net number.

## When to use

Reach for this skill when the user wants the **what-if** answer, not the audit and not the fix:

- "If I fix this finding, what will I save?"
- "Which of these candidate fixes is highest signal?"
- "What's the trade-off — does this regress incrementals?"
- "Predict before I apply; I want to know whether it's worth it."

If the user wants the audit (the *why* answer), use [`ios-build-diagnose`](../ios-build-diagnose/SKILL.md). If they want a fix applied + verified, use `ios-build-fix`. The orchestrator `ios-build-doctor` chains all three; this skill is the predict step.

## Inputs

| Argument | Required | Default | Notes |
| --- | --- | --- | --- |
| `--diagnosis-artifact PATH` | yes | — | Path to a `diagnosis.json` (`ios-build-diagnose` output). |
| `--measurement-artifact PATH` | no | — | Optional path to a `measurement.json` (`ios-build-measure` output). Predictors that consume a baseline (F4 compilation-cache, F5 asset-catalog, F7 oversized-module) use it when supplied; otherwise they fall back to reference data calibrated against Wikipedia-iOS@`9200297c15` + NetNewsWire@`build-comparison-base` (see `references/defaults.md`) with reduced confidence. |
| `--output-dir DIR` | yes | — | Where `simulation.json` is written. |
| `--f6-verified` | no | `false` | Set when the deferred verify has confirmed the Xcode 26 prebuilt-swift-syntax mechanism at line level. Affects the F6 prediction's `tuning_data_point` text but NOT the numeric prediction. |

## Workflow

1. **Load** the diagnosis artifact (required) and the measurement artifact (optional). Schema-validate the diagnosis input shape opportunistically (the artifact ships with `schema_version`).
2. **Build context** — `SimulationContext{diagnosis, measurement, project_path, baseline_clean_seconds, baseline_incremental_seconds}`. Baselines come from `runs.clean.median_seconds` / `runs.incremental.median_seconds` of the benchmark schema.
3. **Bucket findings by rule_id** — concatenate `findings[]` and `additional_recommendations[]`; each bucket holds tuples of `(original_index, finding_dict)`. Indices from `additional_recommendations[]` are emitted as negative numbers (`-1 - rec_index`) so artifact consumers can route back to the source.
4. **Dispatch** — for each rule_id with at least one finding, call the registered predictor under `scripts/simulators/` (registry mapping in `scripts/simulators/registry.py::build_registry()`). A finding whose rule_id has no predictor produces a `predict_unknown` placeholder so the artifact stays complete.
5. **Aggregate** — each predictor consumes ALL findings sharing its rule_id and returns ONE `RulePrediction`. Aggregation strategy is rule-specific (sum for sleep + debug-guard; sqrt-cap for missing-output-declarations; per-file scaling for oversized-module; literal node duration for asset-catalog).
6. **Rank** — most-improvement-first by `clean.estimate_seconds + incremental.estimate_seconds` (most negative = biggest predicted improvement).
7. **Persist** `simulation.json` to `--output-dir/simulation.json` with the schema-validated artifact. Top-level `summary` carries `total_predicted_clean_seconds`, `total_predicted_incremental_seconds`, `top_3_by_clean`, `top_3_by_incremental`.

## Outputs

A single JSON artifact conforming to [`schemas/simulation.schema.json`](../../schemas/simulation.schema.json).

Top-level fields:

- `schema_version` — `"1.0.0"`.
- `tool` — `{name: "ios-build-simulate", version}`.
- `generated_at` — ISO-8601 UTC.
- `inputs` — `{diagnosis_artifact_path, measurement_artifact_path, git_sha, git_branch}`.
- `predictions[]` — one entry per rule_id firing in the diagnosis. Fields per entry:
  - `rule_id`, `family` ∈ {`script-phase`, `build-setting`, `asset-catalog`, `spm`}, `title`.
  - `source_findings: {indices, count}` — pointer back to diagnosis source.
  - `clean: Prediction` and `incremental: Prediction` — separate axes (negative = improvement; positive = regression).
  - `confidence: "high" | "medium" | "low"`.
  - `prerequisites: []` — rule_ids that must be applied first.
  - `applies_when: []` — free-text conditions narrowing the prediction.
  - `notes: []`.
- `summary` — totals + top-3 lists.
- `notes[]` — top-level run notes (missing measurement, unverified F6, predictor gaps).

Each `Prediction` carries `method` ∈ {`measured-on-wikipedia-ios`, `measured-on-netnewswire`, `measurement-derived`, `heuristic`, `literature`}, `estimate_seconds`, `min_seconds`, `max_seconds`, and the **required** `tuning_data_point` string naming the project + run that motivated the prediction.

## Failure modes (what this skill refuses to do)

- **No project mutation.** This skill never edits source files, project.pbxproj, build settings, or Package.resolved. The fix step lives in `ios-build-fix`.
- **No xcodebuild invocation.** This skill is purely an offline transform: diagnosis.json + measurement.json → simulation.json. No build commands run.
- **Predicted, never measured.** Every numeric in the output is labelled `wall_clock_predicted_seconds.method`. The user-facing summary always says "predicted Δ", never "Δ".
- **No silent predictor gaps.** When a diagnosis rule_id has no registered predictor, the orchestrator emits a placeholder `RulePrediction` with `method=heuristic`, `estimate=None`, and a `notes[]` entry calling out the gap. The fix step can decline to act on these.
- **No platform fudge.** `--platform` outside `ios` is rejected by upstream skills (diagnose, measure); the simulate skill inherits the diagnosis's platform field as-is and does not re-validate.
- **No per-target DAG inference.** The benchmark critical-path method is `task-class-aggregate`; per-target DAG attribution is deferred to a v1.x workstream. Predictions consult `references/defaults.md` reference data, NOT the critical-path nodes (except F5, which reads the literal `CompileAssetCatalogVariant` duration).

## Prediction methodology

Each rule's predictor lives under [`scripts/simulators/`](../../scripts/simulators/) and follows this contract:

1. The function signature is `predict(findings, ctx) -> RulePrediction` where `findings` is the slice of `(original_index, finding_dict)` tuples sharing the rule_id and `ctx` is the `SimulationContext`.
2. Aggregation is rule-specific: random-sleep sums, missing-debug-guard sums, missing-output-declarations applies a `sqrt(N) × per_phase` cap to model post-sandbox+fuse parallel fan-out, asset-catalog reads the literal critical-path node, oversized-module scales by source-count.
3. **Every Prediction carries a `tuning_data_point`** naming the project + run that motivated the numeric per AGENTS.md non-negotiable principle 5. Simulate hard-fails the run if any prediction lands without one (the schema's `minLength: 1` enforcement).
4. F4's `clean.estimate_seconds = -0.456 × baseline_clean_seconds`. With `--measurement-artifact`, baseline = `runs.clean.median_seconds`. Without, baseline falls back to a development-time reference of 275s → -125s. v1.0.0 evidence: both Wikipedia-iOS@`9200297c15` and NetNewsWire@`build-comparison-base` ship with `COMPILATION_CACHE_ENABLE_CACHING` unset (universal miss); measured Δ post-fix is reproducible by running `ios-build-fix` against [NetNewsWire](https://github.com/Ranchero-Software/NetNewsWire) and inspecting the generated `fix-F4/fix-result.json` in your local `build-benchmarks/` tree.
5. F6 (`spm/swift-syntax-not-prebuilt`) is gated by `--f6-verified`. The Xcode 26 prebuilt-swift-syntax mechanism is line-level verifiable against the SPA's JSON release-notes endpoint (see [`references/sources.md`](../../references/sources.md) and the verbatim block-quote in [`references/build-settings-best-practices.md`](../../references/build-settings-best-practices.md)).

## References

- Apple [Build Settings Reference](https://developer.apple.com/documentation/xcode/build-settings-reference) — F4 / F9 + PR-#2 audit citations.
- Apple WWDC22 [Demystify parallelization in Xcode builds (110364)](https://developer.apple.com/videos/play/wwdc2022/110364/) — F1, F2, F3, F8 + PR-#2 sandboxing/fuse citations.
- Apple [Xcode 26 Release Notes](https://developer.apple.com/documentation/xcode-release-notes/xcode-26-release-notes) — F6 prebuilt-swift-syntax mechanism (verbatim quote in [`references/build-settings-best-practices.md`](../../references/build-settings-best-practices.md), single-line `>` form so `grep -F` byte-identity holds against the SPA's JSON endpoint).
- This skill bundles its own copy of `scripts/`, `schemas/`, and `references/`. Verify drift with `python3 scripts/verify-sync.py` from the repo root.

## Citation index + thresholds

- [`references/sources.md`](../../references/sources.md) — every URL with verification date.
- [`references/build-settings-best-practices.md`](../../references/build-settings-best-practices.md) — Why / Recommended / Measurement / Risk for COMPILATION_CACHE_ENABLE_CACHING, EAGER_LINKING, ENABLE_USER_SCRIPT_SANDBOXING, FUSE_BUILD_SCRIPT_PHASES, IDEPackageEnablePrebuilts.
- [`references/defaults.md`](../../references/defaults.md) — every analyzer threshold + simulate's per-rule prediction-function tuning data points (project + run that motivated each Δ).
- [`references/critical-path-method.md`](../../references/critical-path-method.md) — v1 task-class-aggregate is the formal contract; per-target DAG attribution stays deferred.
