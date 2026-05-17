# iOS Build-Time Optimisation Skills

> **Benchmark, diagnose, simulate, and fix iOS build-time problems** across Xcode, Tuist, and Bazel — recommend-first, citation-required, refuses to claim wins it can't measure.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE) [![Version](https://img.shields.io/badge/version-1.3.0-brightgreen.svg)](CHANGELOG.md) [![Agent Skills](https://img.shields.io/badge/agent--skills-open%20standard-orange.svg)](https://agentskills.io)

Five [Agent Skills](https://agentskills.io) for Claude Code, Cursor, GitHub Copilot, OpenAI Codex, and Windsurf.

## What this is, in plain terms

iOS app builds get slow over time. The usual culprits are misconfigured script phases, build settings left at non-optimal defaults, and SwiftPM packages the build system can't cache. This repo ships five [Agent Skills](https://agentskills.io) — small, citation-backed plug-ins for AI coding agents — that **measure** your build, **diagnose** what's slow, **predict** the impact of a fix, and **apply** that fix on a throwaway git branch before claiming success.

You don't need to know what a "build setting" or a "script phase" is to start. Ask the agent `use ios-build-doctor on this project` and read the report. The skill cites Apple docs / WWDC sessions for every finding so you can verify the *why* without taking the agent's word for it.

Nothing on disk changes until you explicitly invoke `/ios-build-fix`, and even then the change lands on a throwaway git branch with a before-and-after measurement.

## Try it in 30 seconds

```bash
cd ~/Code/MyiOSApp
claude       # or cursor / codex / windsurf
```

Then ask: `use ios-build-doctor on this project`.

On first run, the doctor takes a clean and an incremental build measurement, prints a ranked list of findings (most impactful first), and stops. It asks before any file changes. If you want a specific fix applied + re-measured, invoke `/ios-build-fix` with the rule id from the report — it runs on a throwaway branch and refuses to claim success if the post-fix Δ is null, regressive, or within variance noise.

## What the output looks like

Each row in the table below is one diagnostic rule that fired on NetNewsWire; the `Outcome` column tells you whether the skill claimed the win or refused. `F3`, `F4`, `F9` are short IDs from the [rule catalog](#rule-catalog-f1f9) further down.

NetNewsWire F3 / F4 / F9 — **all three refused to claim success**:

| Rule | Outcome | Clean Δ | Incremental Δ |
| --- | --- | ---: | ---: |
| F3 (`script-phase/missing-output-declarations`, fuse-only) | `refused-regressive` | +6.31 s | +2.92 s |
| F4 (`build-setting/compilation-cache-disabled`) | `refused-noise` | −1.32 s (in variance) | +10.86 s |
| F9 (`build-setting/eager-linking-disabled`) | `refused-regressive` | +3.47 s | +2.06 s |

Reading the outcomes:

- `refused-regressive` — the fix made the build *slower* on every measured axis.
- `refused-noise` — the Δ was non-zero but inside variance; can't statistically claim it as an improvement.

The full outcome vocabulary lives in [Outcome labels](#outcome-labels).

Numbers above are from running this skill against [NetNewsWire](https://github.com/Ranchero-Software/NetNewsWire) at tag `build-comparison-base`, iPhone 17 simulator, Xcode 26.4.1, n=3 repeats per axis. Reproduce on your machine by cloning the same tag and running `ios-build-fix` — the skill writes `build-benchmarks/<project>/{diagnose,fix-F*}/*.json` into your local (gitignored) working tree so you can compare your hardware's measurements against the table above.

*Here's what the doctor's report looks like on a larger project (Wikipedia-iOS).*

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

Wikipedia-iOS pinned at [`9200297c15`](https://github.com/wikimedia/wikipedia-ios/commit/9200297c15).

## Why "refused" is the headline feature

Every iOS dev has seen a "build time fix" blog post that doesn't actually make their build faster. The reason is variance: a clean build's wall-clock varies by several seconds run-to-run, and a 2 s "improvement" can be pure noise. This skill measures the variance, compares it against the post-fix Δ, and refuses to claim a win if the Δ can't beat the noise floor. That refusal is the credibility hinge — if you can't statistically distinguish the improvement from variance, neither can the skill.

The same three rules (F3 / F4 / F9) that refused on NetNewsWire's 28-second baseline predict 45 %+ clean-build improvements on the larger Wikipedia-iOS corpus. Both behaviours are correct; both ship.

## The 5 skills

Decomposed by **user intent**, not by build-system layer.

| Skill | Answers |
| --- | --- |
| [`ios-build-doctor`](skills/ios-build-doctor/) | "Just look at my build and tell me what to do." |
| [`ios-build-measure`](skills/ios-build-measure/) | "How long does it take? What's getting slower?" |
| [`ios-build-diagnose`](skills/ios-build-diagnose/) | "Why is it slow?" |
| [`ios-build-simulate`](skills/ios-build-simulate/) | "What if I do X? — predict before applying." |
| [`ios-build-fix`](skills/ios-build-fix/) | "Apply the approved change. Verify it helped." |

## Rule catalog (F1–F9)

These are the nine rules the diagnose pass surfaces. Each one cites a specific Apple doc or WWDC session for *why* it matters.

| ID | Rule id | What it checks |
| --- | --- | --- |
| F1 | `script-phase/random-sleep` | Build phases that literally `sleep $RANDOM` (sometimes left behind from debugging) |
| F2 | `script-phase/missing-debug-guard` | Symbol-upload phases (Crashlytics / Firebase / Sentry) that run on every build instead of only on release |
| F3 | `script-phase/missing-output-declarations` | Script phases without `outputPaths` declared — Xcode can't fingerprint them, so they always re-run |
| F4 | `build-setting/compilation-cache-disabled` | `COMPILATION_CACHE_ENABLE_CACHING` is not `YES` (Xcode 16+ compilation cache off) |
| F5 | `asset-catalog/incremental-recompile` | Asset catalog (`*.xcassets`) takes ≥ 3 s to recompile on incrementals |
| F6 | `spm/swift-syntax-not-prebuilt` | A SwiftPM dependency pulls `swift-syntax` and the Xcode 26 prebuilt mechanism is bypassed |
| F7 | `spm/oversized-module` | A SwiftPM target has ≥ 200 source files (single-module compilation unit) |
| F8 | `script-phase/swiftlint-on-build` | SwiftLint running as a build phase (vs as a separate scheme action) |
| F9 | `build-setting/eager-linking-disabled` | `EAGER_LINKING` build setting is not `YES` |

Source of truth: [`CHECKS.md`](CHECKS.md) — has `Inspects` / `Fires when` / `Impact` / `Citation` columns. If this README table ever drifts from CHECKS.md, CHECKS.md wins. Per-rule thresholds: [`references/defaults.md`](references/defaults.md).

## Outcome labels

The six `outcome` values that `fix-result.json` can carry after `ios-build-fix` runs:

| Outcome | Meaning |
| --- | --- |
| `success` | Measured Δ beats the variance threshold — claim the win. |
| `refused-null` | Both clean and incremental Δ are `None` — fix had no measurable effect on either axis. |
| `refused-regressive` | Every measured axis got *slower* after the fix. |
| `refused-noise` | The Δ was non-zero but inside variance — can't distinguish from natural build-time spread. |
| `refused-apply-error` | The fixer couldn't mutate the project (e.g. regex didn't match expected pattern). |
| `refused-benchmark-error` | `xcodebuild` crashed during pre- or post-measurement. |

All of these are emitted by [`ios-build-fix`](skills/ios-build-fix/SKILL.md) into `fix-result.json`.

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

## Side-effects — `ios-build-fix`

Only `ios-build-fix` modifies your project.

It ships with `disable-model-invocation: true` so Claude Code only runs it when you invoke `/ios-build-fix` explicitly.

If your tool doesn't honour that flag (Copilot / Codex / Windsurf currently don't gate the same way), don't let the model fire it autonomously — invoke yourself after reviewing the doctor's recommendation.

Every change goes to a throwaway git branch. Re-measure runs after. Refuses on null / regressive delta.

## v1.0.0 evidence

- **NetNewsWire** @ `build-comparison-base` (upstream: [Ranchero-Software/NetNewsWire](https://github.com/Ranchero-Software/NetNewsWire)) — pure Xcode + F3/F4/F9 fix-apply target. On a 28-second baseline all three rules refused to claim measurable wins; variance noise dominated. That refusal is the gate working as designed (see [Why "refused" is the headline feature](#why-refused-is-the-headline-feature)).
- **Wikipedia-iOS** @ [`9200297c15`](https://github.com/wikimedia/wikipedia-ios/commit/9200297c15) — pure Xcode + Tuist-migration POC at `113cbb6f26`. The same rules predict 45 %+ clean-build improvements on the larger codebase.
- **Bazel** (v1.3.0) — full doctor loop ships end-to-end. Measure → diagnose → simulate → fix all run against [`tests/bazel-smoke-ios/`](tests/bazel-smoke-ios/). Enhanced v1.3 fixture: clean median 19.898 s (spread 12.04 %), incremental 0.136 s; critical path 2 nodes (`bazel-critical-path` method), longest chain 9.75 s; diagnose fires 4 findings (F1 + F3 + F8 on the `LintAndStamp` genrule, F6 on the LocalPkg `Package.resolved`); F4 / F9 / sandboxing / fuse are correctly suppressed (Xcode-only settings have no Bazel analogue). Bazel-aware F1/F3 fixers ship as informational stubs with manual recipes; buildozer-backed auto-apply lands in v1.4.
- **Tuist** (v1.3.0) — full doctor loop ships end-to-end against [`tests/tuist-smoke-ios/`](tests/tuist-smoke-ios/). `tuist_adapter.measure()` runs `tuist generate --no-open` (via `mise exec` when needed) then delegates to `xcode_adapter` against the generated `*.xcworkspace`. Measurement: clean 2.258 s, incremental 1.811 s. Diagnose fires 2 findings + 2 recommendations (Tuist generates a real xcconfig, so F4/F9/sandboxing/fuse fire as on a stock Xcode project).

## Roadmap (v1.4)

Annotated `(deferred to v1.4)` in [`CHANGELOG.md`](CHANGELOG.md).

- **F1 magnitude** — neither public corpus has a `sleep $RANDOM` pattern; awaits a triggering project.
- **F2 measured Δ** — ships as informational manual recipe; auto-apply Δ awaits a triggering artifact-upload phase.
- **F5 Bazel matcher** — `CompileAssetCatalogVariant` task class name is Xcode-only; Bazel chrome-trace uses different action names. v1.4 adds a Bazel-aware matcher so F5 fires on Bazel projects too.
- **F6 magnitude on real-world projects** — the smoke target's swift-syntax pin is fixture; awaits a macro-using public corpus measurement.
- **Bazel auto-apply fixers** — F1 / F3 ship as informational stubs in v1.3. v1.4 adds buildozer-backed BUILD.bazel rewriters.
- **wikipedia-ios Bazel real-corpus measurement** — paused at the WMF Framework Swift↔Obj-C interop cycle (architectural refactor in the upstream codebase, not skill-side work).
- **Visual assets** (banner, doctor-loop GIF, screenshot) — separate patch.

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
