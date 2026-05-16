# Default thresholds — `references/defaults.md`

> Every threshold the analyzers use traces back to a project + run that
> motivated it. AGENTS.md non-negotiable principle 5: "every threshold
> (variance, regression sensitivity, simulation rule magnitude) cites
> the project + run that motivated it."
>
> **v1.0.0 evidence sources.** Thresholds were tuned during development
> against an internal iOS app and re-cited for the public release
> against measurements on:
>
> - **Wikipedia-iOS** at baseline commit
>   [`9200297c15`](https://github.com/wikimedia/wikipedia-ios/commit/9200297c15)
>   — pure-Xcode `Wikipedia` and `Experimental` schemes; plus a
>   Tuist-migration POC at commit `113cbb6f26`. Analysis in
>   `docs/wikipedia-ios-analysis.md`. Reproduce by cloning
>   [wikimedia/wikipedia-ios](https://github.com/wikimedia/wikipedia-ios)
>   at that commit and running `ios-build-doctor`.
> - **NetNewsWire** at tag `build-comparison-base`
>   ([upstream](https://github.com/Ranchero-Software/NetNewsWire)) —
>   pure-Xcode second data point + the F3/F4/F9 fix-apply target.
>   Diagnostics in `docs/netnewswire-analysis.md`. Reproduce by cloning
>   the upstream at that tag and running `ios-build-doctor` /
>   `ios-build-fix`.
> - **Bazel evidence** is qualitative-only in v1.0.0 (the Wikipedia-iOS
>   Bazel POC is paused at the WMFData `apple_core_data_model` blocker).
>   Measured Bazel numbers ship in v1.x once the blocker resolves.
>
> A small set of items remains **deferred to v1.1** with explicit
> annotations below: F1 (random-sleep — no triggering pattern observed
> on either project's script phases), F2 measured Δ (informational
> manual recipe in v1.0.0), F6 magnitude (neither project's
> `Package.resolved` pulls swift-syntax — magnitude data needs a
> macro-using project).
>
> Threshold *values* did not change between v1.0.0-rc1 and v1.0.0; only
> the evidence that justifies them was refreshed against publicly
> reproducible measurements.

## `script-phase/missing-output-declarations` — `outputPaths == []`

Rule fires whenever a `PBXShellScriptBuildPhase` declares zero output
paths. There is no numerical threshold; the binary "no outputs ⇒ Xcode
cannot mark the phase up-to-date" is exactly Apple's recommendation in
the [Xcode 14 release notes](https://developer.apple.com/documentation/xcode-release-notes/xcode-14-release-notes)
and [WWDC22 110364](https://developer.apple.com/videos/play/wwdc2022/110364/).

When `alwaysOutOfDate=True` (the user has already opted into "run every
build"), the rule still fires but with `impact_category=medium` instead
of `high` — the user has signalled they know the phase is not skippable,
so the absence of outputs is less of a footgun and more an inefficiency
they may have weighed against ergonomics.

**Reference data.** Wikipedia-iOS@`9200297c15` ships **6
`PBXShellScriptBuildPhase` entries** across the Wikipedia / Staging /
Experimental targets; **5 of 6 declare `alwaysOutOfDate=1`** (the SwiftLint
× 3, the Update Localizations phase, and one additional always-on
phase). Per `docs/wikipedia-ios-analysis.md:74-78` —
`PhaseScriptExecution` totals 6.54 s clean / **5.82 s incremental
(39 % of the 14.93 s incremental wall-clock)**. NetNewsWire@`build-comparison-base`
ships **8 `PBXShellScriptBuildPhase` entries with all 8
`alwaysOutOfDate=1`**, but most have Release-only no-op bodies in Debug;
measured `PhaseScriptExecution` 0.58 s clean / 0.10 s incremental
(`docs/netnewswire-analysis.md:50,64,77-80`). The fix's wall-clock
recovery scales with the script body cost, not the phase count.

## `asset-catalog/incremental-recompile` — `>= 3 seconds`

Rule fires when the benchmark `measurement.json`
`critical_path.incremental` contains a `CompileAssetCatalogVariant`
node whose `duration_seconds` is ≥ 3.0.

**Why 3 seconds.** The threshold is set so that user-actionable cases
("asset catalog should be cached; something is invalidating it on every
build") trigger the finding while shorter runs that fall within typical
incremental noise do not. A higher threshold (e.g. 5.0 s) would miss
real cases where the user is paying 3–5 s per incremental for a
recurring catalog recompile.

**Reference data.** Wikipedia-iOS@`9200297c15` clean budget
includes **53.86 s `CompileAssetCatalogVariant` across 4 catalogs**
(`Wikipedia/Images.xcassets`, `WMF Framework/.../WMF Framework.xcassets`,
`Wikipedia Stickers/Stickers.xcassets`,
`Widgets/Extension/Assets.xcassets`,
`WMFComponents/.../Assets.xcassets`); see
`docs/wikipedia-ios-analysis.md:40,102-107`. NetNewsWire@`build-comparison-base`
clean budget includes **15.35 s across 3 catalogs**
(`docs/netnewswire-analysis.md:40,109-112`). Neither project's
incremental run exceeded 3.0 s in the captured baselines (asset catalogs
are hit on clean, not invalidated incrementally on these projects), so
both serve as **negative-control** cases for this incremental rule;
the 3.0 s threshold is unchanged.

## `spm/oversized-module` — `source_count >= 200 .swift files`

Rule fires when a local Package.swift module has ≥ 200 Swift source
files. Above ~200 files, single-file edits start triggering meaningfully
larger recompile cones (a one-line change in a module re-emits that
module, and on incremental builds the per-module emit dominates).

**Reference data.** Wikipedia-iOS@`9200297c15` provides the **positive
control**: `WMFComponents` = **213 .swift files** (just over the 200
threshold; the rule fires) and `WMFData` = 103 (does not). NetNewsWire@`build-comparison-base`
provides the **negative control**: 14 internal SPM packages, **largest
`Account` = 111 .swift files; none cross the threshold** (so the rule
does not fire). Direct threshold validation — see
`docs/wikipedia-ios-analysis.md:90` and
`docs/netnewswire-analysis.md:91-101`.

**Open follow-up.** A per-file recompile factor (seconds per source
file in the touched module) is tuned in `scripts/simulators/spm_graph.py`
against measured incremental spans inside oversized modules. The
threshold itself stays at 200 unless additional projects disagree.

## `spm/swift-syntax-not-prebuilt` — pin presence

Rule fires whenever any reachable `Package.resolved` contains a pin with
`identity == "swift-syntax"`. There is no version threshold yet; the
existence of the pin is the trigger.

**Reference data.** Neither v1.0.0 corpus pulls swift-syntax: NetNewsWire's
external SPM pins are Sparkle / PLCrashReporter / Tidemark / Zip
(`docs/netnewswire-analysis.md:103-107`); Wikipedia-iOS's Tuist-cached
17 external xcframeworks are CocoaLumberjack / RxSwift / SDWebImage /
HCaptcha / Logging / WMF* (verifiable by running `ios-build-doctor`
against [wikipedia-ios](https://github.com/wikimedia/wikipedia-ios)@`9200297c15`
and inspecting the generated diagnose artifact's `external_packages`
field). The rule's *detection* (presence of the pin) is correct
by construction; the *magnitude* citation is **deferred to v1.1**
against a project that actually pulls swift-syntax (e.g. a SwiftFormat
- or SwiftFormat-using app). The Xcode 26 prebuilt-syntax mechanism is
verified at line level via Apple's release notes (cited in
`references/build-optimization-sources.md`).

## `spm/branch-pinned` — `branch != null AND version == null`

Rule fires only when a pin's state is `{branch: <name>, version: null}`.

**Reference data.** Both v1.0.0 corpora have **zero branch-pinned
entries** in their Package.resolved files: Wikipedia-iOS uses
version-pinned remote SPMs (per the Tuist Phase-1 commit `113cbb6f26`
notes); NetNewsWire's only non-version-pinned external is `Zip` at
commit hash `059e734` — that is *commit-pinned*, not *branch-pinned*,
so the rule correctly does not fire (`docs/netnewswire-analysis.md:107`).
Both serve as **negative-control** cases; the rule fires when any branch
pin is present.

## `script-phase/missing-debug-guard` — heuristic keyword list

Rule fires when a phase (or a script the phase invokes via
`bash $SRCROOT/...sh`) mentions one of:

`firebase`, `crashlytics`, `upload`, `dsym`, `fullstory`, `datadog`,
`sentry`, `bugsnag`

…AND the script body has no `CONFIGURATION` reference. This is a
heuristic, not a hard rule: a non-upload phase that happens to mention
"firebase" in a comment can false-positive. The finding emits a
`confidence` cue in `notes[]` and accepts ≤1 borderline hit per project
(typical: a `firebase-ios-sdk/Crashlytics/run` invocation that triggers
on the path-based keyword match rather than a feature mention; user
reviewer judges whether the phase is a real upload).

**Reference data.** Neither Wikipedia-iOS nor NetNewsWire ships an
artifact-upload script phase matching the keyword list (Wikipedia's
script phases are SwiftLint × 3 + Update Localizations + 1 other always-on
phase; NetNewsWire's are mostly Release-only no-ops in Debug, plus a
`Verify No Build Settings` Swift script). False-positive rate observed:
**0 of 14 phases combined** — well under the 20 % gate. Magnitude data
(~1.5 s per finding aggregated) is **deferred to v1.1** against a
project that actually triggers F2; v1.0.0 ships F2 as an informational
manual recipe (per `skills/ios-build-fix/SKILL.md` "Auto-applicable
surface" table).

## Simulate prediction tuning data points

`ios-build-simulate` builds a per-rule prediction function on top of
the diagnose finding's `wall_clock_predicted_seconds` block. Each
predictor cites a tuning data point on both clean and incremental
axes per AGENTS.md non-negotiable principle 5. The tuning points are
encoded in the predictor source under
[`scripts/simulators/`](../scripts/simulators/) and reproduced here as
the human-facing table.

| rule_id | tuning data point (clean) | tuning data point (incremental) |
| --- | --- | --- |
| `script-phase/random-sleep` | (deferred to v1.1) — calibrated heuristic; no `sleep $RANDOM` pattern observed in Wikipedia-iOS or NetNewsWire script phases. Estimated mean ~5.5 s, range 1–10 s for a `sleep $(( RANDOM % 10 ))` pattern; backfill awaits a triggering project | same — sleep runs unconditionally on every build |
| `script-phase/missing-debug-guard` | (deferred to v1.1) — informational manual recipe in v1.0.0 surface; no triggering artifact-upload phase observed in Wikipedia-iOS or NetNewsWire. Estimated ~1.5 s per finding aggregated | same — guard fires regardless of clean/incremental |
| `script-phase/missing-output-declarations` | measured-on-wikipedia-ios@`9200297c15` (5 of 6 phases `alwaysOutOfDate=1`; PhaseScriptExecution 6.54 s clean / 5.82 s incremental — 39 % of wall-clock). Per-phase estimate kept at ~4 s ±1 (conservative upper bound; Wikipedia mean is ~2.18 s/phase but development-time heavier-body phases set the bound). Sum capped at `sqrt(N)×4` to model post-sandbox+fuse parallel fan-out | same shape; cap applies symmetrically |
| `script-phase/swiftlint-on-build` | measured-on-wikipedia-ios@`9200297c15` — 3× SwiftLint phases on Wikipedia/Staging/Experimental targets, PhaseScriptExecution 5.82 s incremental = 39 % of wall-clock = 22× the SwiftCompile of the touched file (`docs/wikipedia-ios-analysis.md:55,76`). Estimate kept at ~3 s clean / ~2 s incremental (1–6 range) per-phase; heuristic, project-shape sensitive | measured-on-wikipedia-ios@`9200297c15` ~2 s per finding |
| `build-setting/compilation-cache-disabled` | measured-on-wikipedia-ios@`9200297c15` + netnewswire@`build-comparison-base` — both ship with `COMPILATION_CACHE_ENABLE_CACHING` unset (universal miss; `wikipedia-ios-analysis.md:87`, `netnewswire-analysis.md:89`). Warm-cache clean improvement estimate kept at ~45 % on a baseline that does not yet enable caching; when `measurement.json` supplies a project baseline, prediction scales to that baseline. Measured Δ reproducible by running `ios-build-fix` against [NetNewsWire](https://github.com/Ranchero-Software/NetNewsWire)@`build-comparison-base` (v1.0.0: clean −1.32 s within variance, incremental +10.86 s cache-invalidation cost) | measured-on-wikipedia-ios + netnewswire — incremental regression cost ~10 s positive (cache invalidation cone wider than Xcode's incremental tracker) |
| `build-setting/eager-linking-disabled` | measured-on-wikipedia-ios@`9200297c15` + netnewswire@`build-comparison-base` — both ship with `EAGER_LINKING` unset (universal miss; `wikipedia-ios-analysis.md:86`, `netnewswire-analysis.md:88`). Predicted near-zero with ±8 s spread to surface low confidence; the fixer must refuse on null delta. Designed null-delta refusal-path test reproducible by running `ios-build-fix` against [NetNewsWire](https://github.com/Ranchero-Software/NetNewsWire)@`build-comparison-base` (v1.0.0: `refused-regressive`, clean Δ +3.47 s, incremental Δ +2.06 s — variance floor on a 28 s baseline) | 0 s ±0 — eager linking affects scheduling, not incremental wall-clock |
| `build-setting/script-sandboxing-disabled` (PR-#2) | WWDC22 110364: indirect; estimate=null; wins materialise via `FUSE_BUILD_SCRIPT_PHASES` once enabled | same |
| `build-setting/fuse-build-script-phases-disabled` (PR-#2) | WWDC22 110364: heuristic 0.5 s clean / 0.4 s incremental per phase × project phase count. Wikipedia-iOS@`9200297c15` reference count = 6 phases; NetNewsWire@`build-comparison-base` = 8 phases. Heuristic, project-shape sensitive | same — heuristic, project-shape sensitive |
| `asset-catalog/incremental-recompile` | Incremental-only — predicted 0 s clean (asset catalog always compiles cold; reference Wikipedia 53.86 s / 4 catalogs and NetNewsWire 15.35 s / 3 catalogs both fall in the cold budget) | measurement.json `incremental.critical_path.nodes` `CompileAssetCatalogVariant` duration_seconds — predictor uses literal node duration when supplied. Fallback reference value tuned during development; both v1.0.0 corpora's incremental runs were below the 3.0 s threshold (negative controls) |
| `spm/swift-syntax-not-prebuilt` | (magnitude deferred to v1.1) — neither Wikipedia-iOS nor NetNewsWire pulls swift-syntax (per Package.resolved inspection); estimate kept at ~12 s ±7 with `confidence=low` (heuristic; project-shape sensitive). Backfill awaits a macro-using project | 0 s — clean-build finding; swift-syntax compiles once |
| `spm/oversized-module` | measured-on-wikipedia-ios@`9200297c15` — `WMFComponents = 213 .swift` files (positive control; rule fires) vs `WMFData = 103` (does not); per-file emit ~0.05 s clean. NetNewsWire@`build-comparison-base` largest = `Account` 111 files (negative control; none over threshold). 200-file threshold validated by both observations | 0 s default — incremental cost only materialises on file-level edits inside the module (captured in `applies_when`) |
