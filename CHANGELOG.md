# Changelog

All notable changes to this project. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — v1.0.0 (Phase B)

Phase B closes Phase A's `TODO(public-cite: <project>)` markers by re-running
the diagnose pass against three public iOS projects:

- **Wikipedia-iOS** — Tuist build-system citations
- **NetNewsWire** — pure-Xcode build-system citations
- **Telegram-iOS** — Bazel build-system citations

Threshold values themselves do not change between v1.0.0-rc1 and v1.0.0;
only the evidence that justifies them is refreshed against publicly
reproducible measurements.

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
5. Every threshold cites a project + run that motivated it (the Phase B
   citation backfill closes the `TODO(public-cite: ...)` markers).
6. Honesty about predictions — labelled "predicted Δ", refused on null
   or regressive measured delta after a fix.

### Calibration disclosure

The threshold values and per-rule predictor magnitudes were tuned
during development against a private iOS app. Phase A (this release)
strips that internal evidence and replaces it with
`TODO(public-cite: <project>)` markers so reviewers can see exactly
which citations are pending. Threshold values are unchanged by the
backfill in Phase B.

### Known limitations

- Per-rule magnitude citations carry `TODO(public-cite: <project>)`
  markers pending Phase B's public-project benchmark runs.
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
