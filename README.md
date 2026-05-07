# iOS Build-Time Optimisation Skills

A suite of [Agent Skills](https://agentskills.io) that benchmark, diagnose, simulate, and fix iOS build-time problems across **Xcode**, **Tuist**, and **Bazel** projects. Works in Claude Code, Cursor, GitHub Copilot, OpenAI Codex, and Windsurf — anywhere the [Agent Skills open standard](https://agentskills.io/specification) is supported.

> **Status — v1.0.0.** Per-rule magnitude calibrations cite measured runs on [Wikipedia-iOS](https://github.com/wikimedia/wikipedia-ios) at commit `9200297c15` (pure Xcode `Wikipedia` and `Experimental` schemes; plus a Tuist-migration POC at `113cbb6f26`) and [NetNewsWire](https://github.com/Ranchero-Software/NetNewsWire) at the same Phase B baseline (pure Xcode second data point + the F3/F4/F9 fix-apply target). Bazel adapter ships with qualitative-only evidence in v1.0.0 (the Wikipedia-iOS Bazel POC is paused at the WMFData `apple_core_data_model` blocker; see `references/defaults.md`); measured Bazel numbers ship in v1.x. Threshold *values* did not change between rc1 and v1.0.0 — only the evidence that justifies them. A small set of rules ship with explicit `(deferred to v1.1)` annotations: F1 (no triggering pattern observed in either corpus), F2 measured Δ (informational manual recipe in v1.0.0), F6 magnitude (neither corpus pulls swift-syntax).

## The 5 skills

Decomposed by **user intent**, not by build-system technology layer.

| Skill | Answers the question | When to use |
| --- | --- | --- |
| [`ios-build-doctor`](skills/ios-build-doctor/) | "Just look at my build and tell me what to do." | Entry-point. Runs the questionnaire, detects the build system, dispatches the right specialist, ranks findings by wall-clock impact, asks for approval, hands off to the fixer, re-measures. |
| [`ios-build-measure`](skills/ios-build-measure/) | "How long does my build actually take, and what's getting better/worse over time?" | Benchmark + critical-path attribution + cross-run regression history. |
| [`ios-build-diagnose`](skills/ios-build-diagnose/) | "Why is it slow?" | Unified analyzer: project settings + script phases + Swift compile hotspots + SPM/BUILD graph — single tool, build-system-aware. |
| [`ios-build-simulate`](skills/ios-build-simulate/) | "What happens if I do X before I do it?" | Heuristic predictor for fix impact. Recommend-first, no project mutation. |
| [`ios-build-fix`](skills/ios-build-fix/) | "OK, apply this approved change and verify it actually helped." | Patcher that touches only what was approved, then re-measures and refuses if delta is null/regressive. **Side effects**: see the per-tool note below. |

## Differentiators

1. **Multi-build-system** — Xcode (primary), Tuist, Bazel via an internal adapter pattern. Same diagnostics, three backends.
2. **Wall-clock attribution** — `scripts/critical_path.py` walks the build-timing DAG so findings are ranked by what *actually* prolongs the build, not by cumulative compile aggregates.
3. **Cited recommendations** — every diagnose finding cites a primary source (Apple docs, WWDC session, Tuist docs, Bazel docs). No hand-wavy "this should be faster" reasoning.
4. **Cross-run regression history** — `.build-history/` JSON-flat per-project DB keyed by git SHA; flags regressions over a sliding window.
5. **Honest predictions** — predicted Δ wall-clock per finding, labelled as prediction not measurement, with predicted-vs-actual reporting after every applied fix and refusal-on-null-delta when the fix doesn't help.

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

## Sample run

The doctor's actual transcript on Wikipedia-iOS@`9200297c15` (Experimental scheme, iPhone 17 simulator, Xcode 26.4.1):

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

Per-rule citation evidence is in [`references/defaults.md`](references/defaults.md). v1.0.0 includes the F3 / F4 / F9 fix-apply pass on NetNewsWire — all three honestly **refused to claim success** because the variance noise on a 28-second baseline exceeds the predicted-win magnitude:

| Rule | Outcome | Clean Δ | Incremental Δ |
| --- | --- | ---: | ---: |
| F3 (`script-phase/missing-output-declarations`, fuse-only) | `refused-regressive` | +6.31 s | +2.92 s |
| F4 (`build-setting/compilation-cache-disabled`) | `refused-noise` | −1.32 s (within variance) | +10.86 s (expected regression) |
| F9 (`build-setting/eager-linking-disabled`, designed null-delta) | `refused-regressive` | +3.47 s | +2.06 s |

Source artefacts: [`build-benchmarks/netnewswire/fix-F3/fix-result.json`](build-benchmarks/netnewswire/fix-F3/fix-result.json), [`fix-F4/fix-result.json`](build-benchmarks/netnewswire/fix-F4/fix-result.json), [`fix-F9/fix-result.json`](build-benchmarks/netnewswire/fix-F9/fix-result.json). The refusals are evidence that the fixer's "refuse-on-noise / refuse-on-regression" gate works as designed — these fixes have known wins on larger projects (the development-time corpus + the Wikipedia-iOS pure-Xcode 89.838 s clean baseline confirm the rule applies) but lack the magnitude headroom to detect them on a 28-second baseline. See [`CHECKS.md`](CHECKS.md) for the full set of rules and citations.

## Side-effects warning — `ios-build-fix`

`ios-build-fix` is the only skill that modifies your project. Per the recommend-first design, it ships with `disable-model-invocation: true` so Claude Code will only run it when **you** invoke it explicitly with `/ios-build-fix`. If your tool does not honour `disable-model-invocation` (Copilot, Codex, and Windsurf currently don't gate model invocations the same way), do not let the model run `ios-build-fix` autonomously — invoke it yourself after reviewing the doctor's recommendation.

`ios-build-fix` also runs every change against a throw-away git worktree first, re-measures, and refuses to claim success on a null or regressive delta.

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
├── assets/                       # Banner + screenshots
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

Issues and PRs welcome. Read `AGENTS.md` first — it documents the non-negotiable engineering principles (recommend-first, wall-clock-as-primary-metric, citation-required, refusal-on-null-delta) that all changes must respect.

## License

MIT — see [`LICENSE`](LICENSE).
