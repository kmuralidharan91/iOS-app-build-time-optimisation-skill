# iOS Build-Time Optimisation Skills

> **Benchmark, diagnose, simulate, and fix iOS build-time problems** across Xcode, Tuist, and Bazel — recommend-first, citation-required, refuses to claim wins it can't measure.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE) [![Version](https://img.shields.io/badge/version-1.0.0-brightgreen.svg)](CHANGELOG.md) [![Agent Skills](https://img.shields.io/badge/agent--skills-open%20standard-orange.svg)](https://agentskills.io)

Five [Agent Skills](https://agentskills.io) for Claude Code, Cursor, GitHub Copilot, OpenAI Codex, and Windsurf. v1.0.0's three NetNewsWire fix-applies all refused to claim success — variance noise on a 28-second baseline beat the predicted-win magnitude. That refusal is the gate working as designed.

## v1.0.0 evidence

- **Wikipedia-iOS** @ [`9200297c15`](https://github.com/wikimedia/wikipedia-ios/commit/9200297c15) — pure Xcode + Tuist-migration POC at `113cbb6f26`.
- **NetNewsWire** @ `build-comparison-base` — pure Xcode + F3/F4/F9 fix-apply target.
- **Bazel** — adapter ships, qualitative-only evidence; measured Δ deferred to v1.x.

## The 5 skills

Decomposed by **user intent**, not by build-system layer.

| Skill | Answers |
| --- | --- |
| [`ios-build-doctor`](skills/ios-build-doctor/) | "Just look at my build and tell me what to do." |
| [`ios-build-measure`](skills/ios-build-measure/) | "How long does it take? What's getting slower?" |
| [`ios-build-diagnose`](skills/ios-build-diagnose/) | "Why is it slow?" |
| [`ios-build-simulate`](skills/ios-build-simulate/) | "What if I do X? — predict before applying." |
| [`ios-build-fix`](skills/ios-build-fix/) | "Apply the approved change. Verify it helped." |

Rule catalog: [`CHECKS.md`](CHECKS.md). Per-rule defaults: [`references/defaults.md`](references/defaults.md).

## Key features

- **Multi-build-system** — Xcode, Tuist, Bazel via one adapter pattern.
- **Wall-clock attribution** — findings ranked by what *actually* prolongs the build (DAG walk), not cumulative compile aggregates.
- **Cited recommendations** — every finding cites Apple docs / WWDC / Tuist / Bazel.
- **Cross-run regression history** — `.build-history/` JSON-flat DB keyed by git SHA.
- **Honest predictions** — labelled as predictions, not measurements; refuses on null / regressive delta after a fix.

**Fits next to** `xcodebuild -showBuildTimingSummary` and [XCLogParser](https://github.com/MobileNativeFoundation/XCLogParser). Complements them with rule-based diagnosis and a refuse-on-noise gate.

## Install

### Claude Code

```text
/plugin marketplace add kmuralidharan91/iOS-app-build-time-optimisation-skill
/plugin install ios-build-skills@ios-build-skills
```

### Cursor

Settings → Rules → **Remote Rule (Github)**:

```text
https://github.com/kmuralidharan91/iOS-app-build-time-optimisation-skill
```

### GitHub Copilot

```bash
gh skill install kmuralidharan91/iOS-app-build-time-optimisation-skill
```

### OpenAI Codex

```bash
git clone https://github.com/kmuralidharan91/iOS-app-build-time-optimisation-skill .agents/iOS-build-skills
ln -s .agents/iOS-build-skills/skills .agents/skills
```

### Windsurf

Drop `skills/<name>/` into `.windsurf/skills/`. Activate with `@ios-build-doctor`.

## Quick start (30s)

```bash
cd ~/Code/MyiOSApp
claude       # or cursor / codex / windsurf
```

Then ask: `use ios-build-doctor on this project`.

The doctor benchmarks, diagnoses, ranks findings, and asks before any file changes. `ios-build-fix` runs on a throwaway branch, re-measures, and refuses if the win is null or regressive.

## Sample run

NetNewsWire F3 / F4 / F9 — **all three refused to claim success**:

| Rule | Outcome | Clean Δ | Incremental Δ |
| --- | --- | ---: | ---: |
| F3 (`script-phase/missing-output-declarations`, fuse-only) | `refused-regressive` | +6.31 s | +2.92 s |
| F4 (`build-setting/compilation-cache-disabled`) | `refused-noise` | −1.32 s (in variance) | +10.86 s |
| F9 (`build-setting/eager-linking-disabled`) | `refused-regressive` | +3.47 s | +2.06 s |

Source: [`fix-F3`](build-benchmarks/netnewswire/fix-F3/fix-result.json), [`fix-F4`](build-benchmarks/netnewswire/fix-F4/fix-result.json), [`fix-F9`](build-benchmarks/netnewswire/fix-F9/fix-result.json).

These fixes have known wins on larger projects. On a 28-second baseline, the variance floor wins. Refusing on noise is by design.

The doctor on Wikipedia-iOS @ [`9200297c15`](https://github.com/wikimedia/wikipedia-ios/commit/9200297c15):

```text
> use ios-build-doctor on this project

[ios-build-doctor]
  Detected build system: Xcode (Wikipedia.xcodeproj)
  benchmark: clean 85.4 s, incremental 14.9 s
  diagnose:
    F8 swiftlint-on-build  — high     (5.82 s = 39 % of incremental)
    F4 compilation-cache   — high     (predicted ~45 % clean win)
    F3 missing-output-decl — high     (5/6 phases alwaysOutOfDate)
    F7 oversized-module    — medium   (WMFComponents = 213, > 200)
    F5 asset-catalog       — info     (4 catalogs, 53.86 s clean)
    F9 eager-linking       — low      (designed null-delta test)
  Top recommendation: F4 (warm-cache compilation cache).
```

## Side-effects — `ios-build-fix`

Only `ios-build-fix` modifies your project.

It ships with `disable-model-invocation: true` so Claude Code only runs it when you invoke `/ios-build-fix` explicitly.

If your tool doesn't honour that flag (Copilot / Codex / Windsurf currently don't gate the same way), don't let the model fire it autonomously — invoke yourself after reviewing the doctor's recommendation.

Every change goes to a throwaway git branch. Re-measure runs after. Refuses on null / regressive delta.

## Roadmap (v1.1)

Annotated `(deferred to v1.1)` in [`references/defaults.md`](references/defaults.md); full notes in [`CHANGELOG.md`](CHANGELOG.md).

- **F1 magnitude** — neither corpus has a `sleep $RANDOM` pattern; awaits a triggering project.
- **F2 measured Δ** — ships as informational manual recipe; auto-apply Δ awaits a triggering artifact-upload phase.
- **F6 magnitude** — neither corpus pulls swift-syntax; awaits a macro-using project.
- **Bazel measured benchmarks** — Wikipedia-iOS Bazel paused at WMFData CoreData blocker.
- **Visual assets** (banner, doctor-loop GIF, screenshot) — v1.0.1 patch.

## Repository layout

```text
.
├── LICENSE                  # MIT, (c) 2026 Muralidharan Kathiresan
├── README.md
├── AGENTS.md                # Engineering principles + sync strategy
├── CHECKS.md                # Every diagnose check
├── CHANGELOG.md
├── .claude-plugin/marketplace.json
├── agents/openai.yaml
├── assets/                  # Banner + screenshots (v1.0.1)
├── scripts/                 # Canonical (synced into each skill)
│   ├── adapters/            # xcode / tuist / bazel
│   ├── analyzers/           # diagnose rules
│   ├── simulators/          # simulate predictors
│   ├── fixers/              # fix appliers
│   ├── benchmark.py · diagnose.py · simulate.py · fix.py · doctor.py
│   ├── critical_path.py · history_db.py
│   └── verify-sync.py       # CI gate
├── schemas/
├── references/
└── skills/
    ├── ios-build-doctor/    # SKILL.md only (orchestrator)
    ├── ios-build-measure/
    ├── ios-build-diagnose/
    ├── ios-build-simulate/
    └── ios-build-fix/
```

Each `skills/<name>/` is self-contained, matching the [canonical anthropics/skills layout](https://github.com/anthropics/skills/tree/main/skills/pdf). Sync enforced by `scripts/verify-sync.py`.

## Platform scope

**v1: iOS only.** Adapters carry a `platform` parameter from day one; v2 (macOS / watchOS / tvOS / visionOS) is additive.

## Contributing

Issues and PRs welcome. Read [`AGENTS.md`](AGENTS.md) — engineering principles are non-negotiable: recommend-first, wall-clock primary, citation-required, refusal-on-null-delta.

## License

MIT — see [`LICENSE`](LICENSE).
