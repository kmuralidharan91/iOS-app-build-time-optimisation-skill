# iOS Build-Time Optimisation Skills

> **Benchmark, diagnose, simulate, and fix iOS build-time problems** across **Xcode**, **Tuist**, and **Bazel** — recommend-first, citation-required, and refuses to claim wins it can't measure. Five composable [Agent Skills](https://agentskills.io) that run anywhere the open standard is supported: Claude Code, Cursor, GitHub Copilot, OpenAI Codex, Windsurf.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE) [![Version](https://img.shields.io/badge/version-1.0.0-brightgreen.svg)](CHANGELOG.md) [![Agent Skills](https://img.shields.io/badge/agent--skills-open%20standard-orange.svg)](https://agentskills.io)

**Why it's different.** Every recommendation cites a primary source (Apple docs, WWDC, Tuist, Bazel) and a measured run on a public iOS project. Predictions are labelled "predicted Δ", never "measured". And when a fix doesn't actually help — or the variance noise on a small project drowns the win — the fixer **refuses to claim success** and tells you why. The headline credibility moment in v1.0.0: all three of the F3/F4/F9 fix-applies on NetNewsWire honestly refused to claim success, because a 28-second baseline can't beat its own variance floor. That refusal is the gate working as designed.

> **Status — v1.0.0.** Per-rule magnitude calibrations cite measured runs on [Wikipedia-iOS](https://github.com/wikimedia/wikipedia-ios) at commit [`9200297c15`](https://github.com/wikimedia/wikipedia-ios/commit/9200297c15) (pure Xcode `Wikipedia` and `Experimental` schemes; plus a Tuist-migration POC at `113cbb6f26`) and [NetNewsWire](https://github.com/Ranchero-Software/NetNewsWire) at the same Phase B baseline (pure Xcode second data point + the F3/F4/F9 fix-apply target). Bazel adapter ships with qualitative-only evidence in v1.0.0 (the Wikipedia-iOS Bazel POC is paused at the WMFData `apple_core_data_model` blocker; see [`references/defaults.md`](references/defaults.md)); measured Bazel numbers ship in v1.x. Threshold *values* did not change between rc1 and v1.0.0 — only the evidence that justifies them. A small set of rules ship with explicit `(deferred to v1.1)` annotations: F1 (no triggering pattern observed in either corpus), F2 measured Δ (informational manual recipe in v1.0.0), F6 magnitude (neither corpus pulls swift-syntax). See [Roadmap (v1.1)](#roadmap-v11) below.

## The 5 skills

Decomposed by **user intent**, not by build-system technology layer.

| Skill | Answers the question | When to use |
| --- | --- | --- |
| [`ios-build-doctor`](skills/ios-build-doctor/) | "Just look at my build and tell me what to do." | Entry-point. Runs the questionnaire, detects the build system, dispatches the right specialist, ranks findings by wall-clock impact, asks for approval, hands off to the fixer, re-measures. |
| [`ios-build-measure`](skills/ios-build-measure/) | "How long does my build actually take, and what's getting better/worse over time?" | Benchmark + critical-path attribution + cross-run regression history. |
| [`ios-build-diagnose`](skills/ios-build-diagnose/) | "Why is it slow?" | Unified analyzer: project settings + script phases + Swift compile hotspots + SPM/BUILD graph — single tool, build-system-aware. |
| [`ios-build-simulate`](skills/ios-build-simulate/) | "What happens if I do X before I do it?" | Heuristic predictor for fix impact. Recommend-first, no project mutation. |
| [`ios-build-fix`](skills/ios-build-fix/) | "OK, apply this approved change and verify it actually helped." | Patcher that touches only what was approved, then re-measures and refuses if delta is null/regressive. **Side effects**: see the per-tool note below. |

> **Rule catalog.** Every diagnose rule with citation, threshold, and rationale is in [`CHECKS.md`](CHECKS.md). Per-rule defaults and deferral notes live in [`references/defaults.md`](references/defaults.md).

## Differentiators

1. **Multi-build-system** — Xcode (primary), Tuist, Bazel via an internal adapter pattern. Same diagnostics, three backends.
2. **Wall-clock attribution** — `scripts/critical_path.py` walks the build-timing DAG so findings are ranked by what *actually* prolongs the build, not by cumulative compile aggregates.
3. **Cited recommendations** — every diagnose finding cites a primary source (Apple docs, WWDC session, Tuist docs, Bazel docs). No hand-wavy "this should be faster" reasoning.
4. **Cross-run regression history** — `.build-history/` JSON-flat per-project DB keyed by git SHA; flags regressions over a sliding window.
5. **Honest predictions** — predicted Δ wall-clock per finding, labelled as prediction not measurement, with predicted-vs-actual reporting after every applied fix and refusal-on-null-delta when the fix doesn't help.

**How this fits next to existing tooling.** This skill suite *complements*, not replaces, the tools you already use: `xcodebuild -showBuildTimingSummary` (raw per-target timings from Apple's build system) and [XCLogParser](https://github.com/MobileNativeFoundation/XCLogParser) (parses xcactivitylog binaries for CI dashboards). The skills wrap those signals with rule-based diagnosis, citation-backed recommendations, predicted-vs-actual reporting, and a refuse-on-noise gate — so you get *recommendations you can act on*, not just data.

## Install

Pick the path for your tool. Each one points the agent at the same `skills/` directory in this repo.

### Claude Code

```text
/plugin marketplace add kmuralidharan91/iOS-app-build-time-optimisation-skill
/plugin install ios-build-skills@ios-build-skills
```

Once installed, ask Claude `use ios-build-doctor on this project` from any iOS project directory.

### Cursor

Open Cursor Settings → Rules → **Remote Rule (Github)** and paste:

```text
https://github.com/kmuralidharan91/iOS-app-build-time-optimisation-skill
```

Cursor also auto-discovers the bundled `.claude/skills/` layout if you clone the repo locally and copy `skills/<name>/` into the project's `.cursor/skills/` or your home `~/.agents/skills/`.

### GitHub Copilot

```bash
gh skill install kmuralidharan91/iOS-app-build-time-optimisation-skill
```

Or copy `skills/<name>/` into `.github/skills/` (project-level) or `~/.copilot/skills/` (personal).

### OpenAI Codex

Codex auto-discovers `.agents/skills/` walking up from the cwd. From your iOS project root:

```bash
git clone https://github.com/kmuralidharan91/iOS-app-build-time-optimisation-skill .agents/iOS-build-skills
ln -s .agents/iOS-build-skills/skills .agents/skills
```

(Codex also reads the optional `agents/openai.yaml` shipped in this repo.)

### Windsurf (Cascade)

Drop `skills/<name>/` into `.windsurf/skills/` at your project root, or into `~/.agents/skills/` for cross-project use. Then activate with `@ios-build-doctor` in Cascade.

## Quick start (30 seconds)

After install, from any iOS project root:

```bash
cd ~/Code/MyiOSApp        # your Xcode / Tuist / Bazel project
claude                    # or cursor, codex, etc.
```

Then ask the agent: `use ios-build-doctor on this project`.

The doctor runs a short questionnaire, picks the right adapter (Xcode / Tuist / Bazel), benchmarks clean + incremental builds, diagnoses findings, and surfaces a top-N list ranked by predicted Δ wall-clock. **Nothing on disk changes until you approve a specific finding.** When you do, `ios-build-fix` runs on a throwaway git branch, re-measures, and either reports the measured Δ — or refuses honestly if the win is null, regressive, or within the variance floor.

## Sample run

The credibility headline first. v1.0.0 includes the F3 / F4 / F9 fix-apply pass on NetNewsWire — and **all three honestly refused to claim success** because the variance noise on a 28-second clean baseline exceeds the predicted-win magnitude:

| Rule | Outcome | Clean Δ | Incremental Δ |
| --- | --- | ---: | ---: |
| F3 (`script-phase/missing-output-declarations`, fuse-only) | `refused-regressive` | +6.31 s | +2.92 s |
| F4 (`build-setting/compilation-cache-disabled`) | `refused-noise` | −1.32 s (within variance) | +10.86 s (expected regression) |
| F9 (`build-setting/eager-linking-disabled`, designed null-delta) | `refused-regressive` | +3.47 s | +2.06 s |

Source artefacts: [`build-benchmarks/netnewswire/fix-F3/fix-result.json`](build-benchmarks/netnewswire/fix-F3/fix-result.json), [`fix-F4/fix-result.json`](build-benchmarks/netnewswire/fix-F4/fix-result.json), [`fix-F9/fix-result.json`](build-benchmarks/netnewswire/fix-F9/fix-result.json). The refusals are evidence that the fixer's "refuse-on-noise / refuse-on-regression" gate works as designed — these fixes have known wins on larger projects (the development-time corpus + the Wikipedia-iOS pure-Xcode 89.838 s clean baseline confirm the rule applies) but lack the magnitude headroom to detect them on a 28-second baseline.

For a positive case, here's the doctor's actual transcript on Wikipedia-iOS@[`9200297c15`](https://github.com/wikimedia/wikipedia-ios/commit/9200297c15) (Experimental scheme, iPhone 17 simulator, Xcode 26.4.1):

```text
> use ios-build-doctor on this project

[ios-build-doctor]
  Detected build system: Xcode (Wikipedia.xcodeproj)
  Running benchmark.py --repeats 3 …
    clean median: 85.4 s   (variance 2.7 %)
    incremental median: 14.9 s  (variance 32 %, flagged for re-run)
  Running diagnose.py …
    F8 script-phase/swiftlint-on-build  — high impact   (3× SwiftLint phases; 5.82 s incremental = 39 % of wall-clock)
    F4 build-setting/compilation-cache  — high impact   (COMPILATION_CACHE_ENABLE_CACHING unset; predicted ~45 % clean win)
    F3 script-phase/missing-output-decls — high impact  (5/6 phases alwaysOutOfDate; predicted -7 s clean / -5.6 s incremental fuse-only)
    F7 spm/oversized-module             — medium impact (WMFComponents=213 files, just over 200 threshold)
    F5 asset-catalog/incremental-recompile — informational (4 catalogs, 53.86 s clean cost)
    F9 build-setting/eager-linking-disabled — low impact (designed null-delta refusal-path test)
  Top recommendation: F4 (warm-cache compilation cache).
```

Per-rule citation evidence is in [`references/defaults.md`](references/defaults.md). Full rule catalog with thresholds and primary-source links: [`CHECKS.md`](CHECKS.md).

## Side-effects warning — `ios-build-fix`

`ios-build-fix` is the only skill that modifies your project. Per the recommend-first design, it ships with `disable-model-invocation: true` so Claude Code will only run it when **you** invoke it explicitly with `/ios-build-fix`. If your tool does not honour `disable-model-invocation` (Copilot, Codex, and Windsurf currently don't gate model invocations the same way), do not let the model run `ios-build-fix` autonomously — invoke it yourself after reviewing the doctor's recommendation.

`ios-build-fix` also runs every change against a throw-away git worktree first, re-measures, and refuses to claim success on a null or regressive delta.

## Roadmap (v1.1)

Honest deferrals already documented in [`CHANGELOG.md`](CHANGELOG.md) and annotated `(deferred to v1.1)` in [`references/defaults.md`](references/defaults.md):

- **F1 (`script-phase/random-sleep`) magnitude** — neither Wikipedia-iOS nor NetNewsWire ships a triggering `sleep $RANDOM` pattern. Rule fires on detection; magnitude calibration awaits a project that has one.
- **F2 (`script-phase/missing-debug-guard`) measured Δ** — v1.0.0 ships F2 as informational manual recipe (per-project review required); auto-apply measured Δ awaits a project with a triggering artifact-upload phase.
- **F6 (`spm/swift-syntax-not-prebuilt`) magnitude** — neither v1.0.0 corpus pulls swift-syntax (NetNewsWire externs: Sparkle/PLCrashReporter/Tidemark/Zip; Wikipedia-iOS Tuist: 17 cached external xcframeworks, none swift-syntax). Backfill awaits a macro-using project.
- **Bazel measured benchmarks** — Wikipedia-iOS Bazel POC paused at the WMFData `apple_core_data_model` blocker; v1.0.0 ships Bazel adapter code with qualitative-only evidence. Measured Bazel numbers land once a project builds end-to-end.
- **Visual assets** (`assets/banner.png`, `demo-doctor-loop.gif`, `screenshot-diagnose-output.png`) — v1.0.1 patch.

## Repository layout

```text
.
├── LICENSE                       # MIT, (c) 2026 Muralidharan Kathiresan
├── README.md                     # This file
├── AGENTS.md                     # Engineering principles + sync strategy
├── CHECKS.md                     # Developer-facing summary of every diagnose check
├── CHANGELOG.md                  # What shipped at each version
├── .claude-plugin/
│   └── marketplace.json          # Claude Code marketplace manifest
├── agents/
│   └── openai.yaml               # OpenAI Codex policy/UI metadata
├── assets/                       # Banner + screenshots (v1.0.1)
├── scripts/                      # Canonical scripts (synced into each skill — see AGENTS.md)
│   ├── adapters/                 # xcode / tuist / bazel
│   ├── analyzers/                # diagnose-side rule implementations
│   ├── simulators/               # simulate-side per-rule predictors
│   ├── fixers/                   # fix-side per-rule appliers
│   ├── benchmark.py              # ios-build-measure entry point
│   ├── critical_path.py
│   ├── diagnose.py               # ios-build-diagnose entry point
│   ├── simulate.py               # ios-build-simulate entry point
│   ├── fix.py                    # ios-build-fix entry point
│   ├── doctor.py                 # ios-build-doctor orchestration
│   ├── history_db.py
│   └── verify-sync.py            # CI gate: skill copies match canonical
├── schemas/                      # JSON schemas for benchmark / diagnosis / simulation / fix-result / history
├── references/                   # Durable facts: settings, thresholds, citations
└── skills/
    ├── ios-build-doctor/         # SKILL.md only (orchestrator)
    ├── ios-build-measure/        # SKILL.md + scripts/ + schemas/
    ├── ios-build-diagnose/       # SKILL.md + scripts/ + references/ + schemas/
    ├── ios-build-simulate/       # SKILL.md + scripts/ + references/ + schemas/
    └── ios-build-fix/            # SKILL.md + scripts/ + references/ + schemas/
```

Each `skills/<name>/` directory is self-contained, matching the [canonical anthropics/skills layout](https://github.com/anthropics/skills/tree/main/skills/pdf). Sync from canonical roots is enforced by `scripts/verify-sync.py`.

## Platform scope

**v1: iOS only.** Adapters carry a `platform` parameter from day one so v2 (macOS / watchOS / tvOS / visionOS) is additive, not a rewrite.

## Contributing

Issues and PRs welcome. Read [`AGENTS.md`](AGENTS.md) first — it documents the non-negotiable engineering principles (recommend-first, wall-clock-as-primary-metric, citation-required, refusal-on-null-delta) that all changes must respect.

## License

MIT — see [`LICENSE`](LICENSE).
