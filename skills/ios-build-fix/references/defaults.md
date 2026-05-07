# Default thresholds — `references/defaults.md`

> Every threshold the analyzers use must trace back to a project + run
> that motivated it. AGENTS.md non-negotiable principle 5: "every
> threshold (variance, regression sensitivity, simulation rule
> magnitude) cites the project + run that motivated it."
>
> **Phase A (v1.0.0-rc1) status.** The thresholds below were tuned
> against a private iOS app during development. The citations have
> been redacted; v1.0.0 (the public release) backfills each
> `TODO(public-cite: <project>)` marker with measurements taken
> against a public iOS project — Wikipedia-iOS for the Tuist build
> system, NetNewsWire for the pure-Xcode build system, Telegram-iOS
> for the Bazel build system. Threshold *values* are not changed by
> the citation backfill; only the evidence that justifies them.

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

**Reference data.** TODO(public-cite: NetNewsWire) report the count of
`PBXShellScriptBuildPhase` entries on the public iOS project, and how
many declared neither inputs nor outputs.

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

**Reference data.** TODO(public-cite: NetNewsWire) measure the
`CompileAssetCatalogVariant` `duration_seconds` for incremental Debug
builds and confirm at least one run exceeds 3.0 s. The threshold value
is not changed by the citation backfill.

## `spm/oversized-module` — `source_count >= 200 .swift files`

Rule fires when a local Package.swift module has ≥ 200 Swift source
files. Above ~200 files, single-file edits start triggering meaningfully
larger recompile cones (a one-line change in a module re-emits that
module, and on incremental builds the per-module emit dominates).

**Reference data.** TODO(public-cite: Wikipedia-iOS, NetNewsWire) record
the `.swift` file count per local Package.swift module. The 200-file
cutoff was originally tuned against a private project's modules at 794
and 330 files (oversized cases) versus 161 and 151 files (under
threshold); the public-cite work confirms the threshold against the
public projects' module shapes.

**Open follow-up.** A per-file recompile factor (seconds per source
file in the touched module) is tuned in `scripts/simulators/spm_graph.py`
against measured incremental spans inside oversized modules. The
threshold itself stays at 200 unless additional projects disagree.

## `spm/swift-syntax-not-prebuilt` — pin presence

Rule fires whenever any reachable `Package.resolved` contains a pin with
`identity == "swift-syntax"`. There is no version threshold yet; the
existence of the pin is the trigger.

**Reference data.** TODO(public-cite: NetNewsWire) confirm the project's
`Package.resolved` contains a `swift-syntax` pin (transitive via Swift
macros) and record the resolved version. The Xcode 26 prebuilt-syntax
mechanism is verified at line level via Apple's release notes (cited in
`references/build-optimization-sources.md`).

## `spm/branch-pinned` — `branch != null AND version == null`

Rule fires only when a pin's state is `{branch: <name>, version: null}`.

**Reference data.** TODO(public-cite: NetNewsWire) enumerate every pin
in `Package.resolved` and confirm zero `branch != null AND version ==
null` entries. Branch pins force a fresh fetch on every clean build and
defeat reproducibility; the rule fires when any are present.

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

**Reference data.** TODO(public-cite: NetNewsWire) enumerate every
`PBXShellScriptBuildPhase` whose body matches the keyword list, record
which have a `CONFIGURATION` reference, and which trigger the borderline
path-based match. The strict false-positive rate must stay below the
20% gate.

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
| `script-phase/random-sleep` | TODO(public-cite: NetNewsWire) measure a typical `sleep $RANDOM` invocation and capture mean + range; expected mean ~5.5 s, range 1–10 s for a `sleep $(( RANDOM % 10 ))` pattern | same — sleep runs unconditionally on every build |
| `script-phase/missing-debug-guard` | TODO(public-cite: NetNewsWire) measure incremental cost of unguarded artifact-upload phases; expected ~1.5 s per finding aggregated | same — guard fires regardless of clean/incremental |
| `script-phase/missing-output-declarations` | TODO(public-cite: NetNewsWire) measure per-phase wall-clock cost; expected ~4 s ±1; sum capped at `sqrt(N)×4` to model post-sandbox+fuse parallel fan-out | same shape; cap applies symmetrically |
| `script-phase/swiftlint-on-build` | TODO(public-cite: NetNewsWire) measure SwiftLint build-phase wall-clock; expected ~3 s clean, ~2 s incremental (1–6 range) | TODO(public-cite: NetNewsWire) ~2 s per finding |
| `build-setting/compilation-cache-disabled` | TODO(public-cite: NetNewsWire) measure warm-cache clean improvement; expected ~45 % on a baseline that does not yet enable `COMPILATION_CACHE_ENABLE_CACHING`. When `measurement.json` supplies a project baseline, prediction scales to that baseline | TODO(public-cite: NetNewsWire) measure incremental regression cost; expected ~10 s positive (regression) |
| `build-setting/eager-linking-disabled` | TODO(public-cite: NetNewsWire) measure clean improvement; expected near-zero with ±8 s spread to surface low confidence; the fixer must refuse on null delta | 0 s ±0 — eager linking affects scheduling, not incremental wall-clock |
| `build-setting/script-sandboxing-disabled` (PR-#2) | WWDC22 110364: indirect; estimate=null; wins materialise via `FUSE_BUILD_SCRIPT_PHASES` once enabled | same |
| `build-setting/fuse-build-script-phases-disabled` (PR-#2) | WWDC22 110364: heuristic 0.5 s clean / 0.4 s incremental per phase × project phase count → TODO(public-cite: NetNewsWire) record the project's phase count and resulting expected magnitude | same — heuristic, project-shape sensitive |
| `asset-catalog/incremental-recompile` | Incremental-only — predicted 0 s clean (asset catalog always compiles cold) | TODO(public-cite: NetNewsWire) measurement.json `incremental.critical_path.nodes` `CompileAssetCatalogVariant` duration_seconds; predictor uses literal node duration when supplied, else falls back to the reference value |
| `spm/swift-syntax-not-prebuilt` | TODO(public-cite: NetNewsWire) confirm `swift-syntax` pin and predicted clean improvement once Xcode 26's prebuilt-syntax mechanism applies; expected ~12 s ±7 | 0 s — clean-build finding; swift-syntax compiles once |
| `spm/oversized-module` | TODO(public-cite: Wikipedia-iOS, NetNewsWire) record module file counts and per-file emit cost. Heuristic: per-file emit ~0.05 s clean; an oversized module of N files contributes ~N × 0.05 s | 0 s default — incremental cost only materialises on file-level edits inside the module (captured in `applies_when`) |
