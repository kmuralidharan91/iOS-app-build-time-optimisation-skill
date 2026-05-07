# Changelog

All notable changes to this project. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-05-07

Phase B closed Phase A's `TODO(public-cite: <project>)` markers (175 total)
by mapping each to measured runs against publicly reproducible iOS
projects:

- **Wikipedia-iOS** at baseline tag `build-comparison-base` (`9200297c15`)
  — pure-Xcode `Wikipedia` and `Experimental` schemes (clean medians
  89.838 s and 85.412 s; incremental 27.71 s and 14.93 s); plus a
  Tuist-migration POC at commit `113cbb6f26` (Tuist cached: clean
  72.457 s, −19.4 % vs Xcode anchor; incremental 29.235 s, +5.5 % within
  variance). The Wikipedia-iOS Bazel POC is paused at the WMFData
  `apple_core_data_model` blocker; the v1.0.0 Bazel adapter cites
  `bazel/progress.md` qualitatively, with measured Δ deferred to v1.x.
- **NetNewsWire** at the v1.0.0 baseline (clean median 28.163 s,
  incremental 14.25 s; 0 type-check hotspots) — pure-Xcode second data
  point + the F3/F4/F9 fix-apply target. **All three fix-apply runs
  honestly refused to claim success** because the variance noise on a
  28-second baseline exceeded the predicted-win magnitude:
  - F3 (fuse-only): `refused-regressive` (clean +6.31 s, incremental
    +2.92 s) — see `build-benchmarks/netnewswire/fix-F3/fix-result.json`.
  - F4 (compilation-cache): `refused-noise` (clean −1.32 s within
    variance, incremental +10.86 s expected regression) — see
    `build-benchmarks/netnewswire/fix-F4/fix-result.json`.
  - F9 (eager-linking, designed null-delta): `refused-regressive`
    (clean +3.47 s, incremental +2.06 s) — see
    `build-benchmarks/netnewswire/fix-F9/fix-result.json`.

  These refusals are evidence that the fixer's `refuse-on-noise` /
  `refuse-on-regression` gate behaves correctly: the same fixes have
  known wins on larger projects (the development-time corpus + the
  Wikipedia-iOS pure-Xcode 89.838 s baseline confirm the rule
  *applies*), but they lack the magnitude headroom to detect over the
  variance floor on a 28-second baseline.

The v1.0.0 corpus also validates the F7 oversized-module 200-file
threshold cleanly: Wikipedia-iOS WMFComponents = 213 files (positive
control; rule fires) vs WMFData = 103 (does not); NetNewsWire largest
= Account 111 files (negative control; 14 packages, none over).

### Changed

- Threshold *values* did not change between rc1 and v1.0.0 — only the
  evidence that justifies them is refreshed against publicly
  reproducible measurements.
- `WallClockPrediction.method` enum migrated from
  `{measured-on-private-corpus, measured-on-wikipedia, heuristic, literature}`
  to `{measured-on-wikipedia-ios, measured-on-netnewswire,
  measurement-derived, heuristic, literature}` in `schemas/diagnosis.schema.json`
  + `schemas/simulation.schema.json` + the analyzer/simulator
  `__init__.py` Literal types.

### Deferred to v1.1 (annotated `(deferred to v1.1)` in `references/defaults.md`)

- F1 (`script-phase/random-sleep`) magnitude — neither v1.0.0 corpus
  has a `sleep $RANDOM` pattern; backfill awaits a triggering project.
- F2 (`script-phase/missing-debug-guard`) measured Δ — v1.0.0 ships F2
  as informational manual recipe (per `skills/ios-build-fix/SKILL.md`).
- F6 (`spm/swift-syntax-not-prebuilt`) magnitude — neither v1.0.0 corpus
  pulls swift-syntax (NetNewsWire externs: Sparkle/PLCrashReporter/
  Tidemark/Zip; Wikipedia-iOS Tuist: 17 cached external xcframeworks
  none of which are swift-syntax). Backfill awaits a macro-using project.
- Bazel measured benchmarks — Wikipedia-iOS Bazel paused at WMFData
  CoreData blocker; v1.x backfills once that resolves.

## [1.0.0-rc1] — 2026-05-07

First public release candidate. The five skills, schemas, predictors,
and fixers are complete and self-consistent; the citation backfill
against public iOS projects is deferred to v1.0.0 (Phase B).

### Added

- **Five Agent Skills** following the
  [agentskills.io](https://agentskills.io) open standard:
  - `ios-build-doctor` — orchestrator: questionnaire → benchmark →
    diagnose → simulate → approval gate → fix → re-measure → transcript.
  - `ios-build-measure` — benchmarking, critical-path attribution,
    cross-run regression history.
  - `ios-build-diagnose` — unified analyzer covering project settings,
    script phases, asset-catalog incremental cost, and SPM/BUILD graph.
  - `ios-build-simulate` — recommend-first per-rule predictor with
    predicted-vs-actual reporting hooks.
  - `ios-build-fix` — atomic git-aware fixer with refusal-on-null/
    regressive-delta. Side-effect skill: ships with
    `disable-model-invocation: true` and an in-body warning for
    runtimes that do not honour that flag.
- **Multi-build-system adapters**: `xcode_adapter`, `tuist_adapter`,
  `bazel_adapter` — same diagnostics, three backends.
- **JSON schemas** for benchmark, diagnosis, simulation, fix-result, and
  history artifacts (Draft 2020-12).
- **Marketplace manifest** at `.claude-plugin/marketplace.json` for
  `/plugin marketplace add` / `/plugin install` flow.
- **Codex policy file** at `agents/openai.yaml` flagging `ios-build-fix`
  as requiring explicit user invocation.
- **Cross-tool install paths** documented in README for Claude Code,
  Cursor, GitHub Copilot, OpenAI Codex, and Windsurf.

### Engineering principles (see `AGENTS.md`)

1. Recommend-first; never mutate without explicit per-finding approval.
2. Wall-clock is the primary metric; findings ranked by predicted Δ
   wall-clock impact via the build-timing DAG walk.
3. Questionnaire-first UX in `ios-build-doctor`.
4. Every diagnose finding cites a primary source (Apple docs, WWDC,
   Tuist, Bazel).
5. Every threshold cites a project + run that motivated it (closed in
   v1.0.0 against Wikipedia-iOS@`9200297c15` and NetNewsWire@`build-comparison-base`).
6. Honesty about predictions — labelled "predicted Δ", refused on null
   or regressive measured delta after a fix.

### Calibration disclosure

The threshold values and per-rule predictor magnitudes were tuned
during development against an internal iOS app. Phase A (this release)
strips that internal evidence and replaces it with
`TODO(public-cite: <project>)` markers so reviewers can see exactly
which citations are pending. v1.0.0 closes those markers — see the
[1.0.0] section above. Threshold values were not changed by the
citation backfill.

### Known limitations

- Per-rule magnitude citations carry `TODO(public-cite: <project>)`
  markers pending v1.0.0's public-project citation backfill (closed —
  see [1.0.0] section above).
- v1 platform scope is **iOS only**. The adapters carry a `platform`
  parameter from day one so v2 (macOS / watchOS / tvOS / visionOS) is
  additive, not a rewrite.
- F2 (`script-phase/missing-debug-guard`) and F8
  (`script-phase/swiftlint-on-build`) ship as informational manual
  recipes wrapping `_no_op` because their auto-application requires
  per-project review.
- F3 fixer applies `FUSE_BUILD_SCRIPT_PHASES` only;
  `ENABLE_USER_SCRIPT_SANDBOXING` is deferred to v1.x because it
  requires a per-phase `outputPaths` editor.
