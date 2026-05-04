# Default thresholds — `references/defaults.md`

> Every threshold the analyzers use must trace back to a project + run
> that motivated it. AGENTS.md non-negotiable principle 5: "every
> threshold (variance, regression sensitivity, simulation rule
> magnitude) cites the project + run that motivated it." Phase A simulate
> tunes these on additional projects; Phase A fix re-measures.

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

**Reference data.** REDACTED `develop` @ `REDACTED` had 14
`PBXShellScriptBuildPhase` entries; **5** declared neither inputs nor
outputs and ran on every build (Phase A step 22 CSV). Wikipedia iOS,
Telegram-iOS, NetNewsWire counts to be captured in Phase A+.

## `asset-catalog/incremental-recompile` — `>= 3 seconds`

Rule fires when the Phase A `measurement.json` `critical_path.incremental`
contains a `CompileAssetCatalogVariant` node whose `duration_seconds` is
≥ 3.0.

**Why 3 seconds.** REDACTED 4/26 baseline measured `CompileAssetCatalogVariant
= 8.694s` on incremental Debug+sim. Phase A re-measurement on the same
branch (post-Phase-A) measured 4.366s — still high enough that the
finding is the user-actionable insight ("asset catalog should be cached;
something is invalidating it on every build"). 3.0s catches both
measurements with margin; 5.0s would have missed the Phase A number and
under-reported the finding.

**Reference data.** Two REDACTED runs:
- 4/26 baseline: `8.694s` incremental (optimization-plan.md).
- Phase A measurement (`docs/smoke/1/measurement.json`): `4.366s`.

## `spm/oversized-module` — `source_count >= 200 .swift files`

Rule fires when a local Package.swift module has ≥ 200 Swift source
files. Above ~200 files, single-file edits start triggering meaningfully
larger recompile cones (a one-line change in a module re-emits that
module, and on incremental builds the per-module emit dominates).

**Reference data.** REDACTED `develop` (live checkout, 2026-05-04):
- `REDACTED`: 794 .swift files (`high` impact tier — ≥600 files).
- `REDACTED`: 330 .swift files.
- `REDACTED`: 161 .swift files (under threshold).
- `REDACTED`: 151 .swift files (under threshold).

The 200-file cutoff comes from these four REDACTED modules: 330 and 794 are
the ground-truth oversized cases; 161 and 151 do not appear in the
optimization-plan.md as oversized targets. Setting the floor at 200
captures the two targets the user already considers oversized while not
catching the two below.

**Open follow-up.** Phase A simulate adds a per-file recompile factor
(seconds per source file in the touched module) tuned against measured
incremental spans inside `REDACTED` vs `REDACTED`. The
threshold itself stays at 200 unless additional projects disagree.

## `spm/swift-syntax-not-prebuilt` — pin presence

Rule fires whenever any reachable `Package.resolved` contains a pin with
`identity == "swift-syntax"`. There is no version threshold yet; the
existence of the pin is the trigger.

**Reference data.** REDACTED `Package.resolved:382` pins `swift-syntax @
510.0.3` (Phase A ground truth + Phase A live verify). Without xcodebuild
project-side context, Phase A cannot tell which of REDACTED's 51 pins
transitively imports swift-syntax — Phase A simulate adds the
transitive-importer walk so the fix recommendation can name the package
to talk to.

## `spm/branch-pinned` — `branch != null AND version == null`

Rule fires only when a pin's state is `{branch: <name>, version: null}`.
REDACTED `REDACTED` was previously branch-pinned (R1 in the Phase A
ground truth) and is now `version=0.0.11` — the rule does **not** fire.

**Reference data.** REDACTED `develop` @ `REDACTED`: zero branch-pinned
entries (Phase A verification + Phase A live verify). If the rule ever
fires on `REDACTED` against current develop, the verification
log records it as a regression-of-fix, not a new finding.

## `script-phase/missing-debug-guard` — heuristic keyword list

Rule fires when a phase (or a script the phase invokes via
`bash $SRCROOT/...sh`) mentions one of:

`firebase`, `crashlytics`, `upload`, `dsym`, `fullstory`, `datadog`,
`sentry`, `bugsnag`

…AND the script body has no `CONFIGURATION` reference. This is a
heuristic, not a hard rule: a non-upload phase that happens to mention
"firebase" in a comment can false-positive. Per Phase A plan: emit
`confidence` cue in the finding's `notes[]` and accept ≤1 borderline
hit (REDACTED's `Crashlytics-Run Script` body is `${BUILD_DIR%/Build/*}/
SourcePackages/checkouts/firebase-ios-sdk/Crashlytics/run` — borderline
because the keyword "firebase" comes from a path, not a feature
mention; user reviewer judges whether this is a true F2 hit).

**Reference data.** REDACTED on `develop`:
- `Step 7 - Run FirebaseCrashlytics` → invokes `Step7_RunCrashlytics.sh`
  (no Debug guard) — true F2 hit.
- `Step 8 - Upload Local dSYM` → invokes `Step8_UploadLocalDSYM.sh`
  (no Debug guard) — true F2 hit.
- `Crashlytics-Run Script` → invokes `firebase-ios-sdk/Crashlytics/run`
  binary directly — borderline; guarded by Phase A simulate review.

**Phase A disposition (user-confirmed via AskUserQuestion 2026-05-04).**
The `Crashlytics-Run Script` borderline hit is accepted as a real F2
case rather than a heuristic false positive. Reasoning: Firebase's
`Crashlytics/run` binary uploads dSYMs on every Debug simulator build
unless the project gates the phase via `CONFIGURATION`. The keyword
match arrives via the SourcePackages path
(`${BUILD_DIR%/Build/*}/SourcePackages/checkouts/firebase-ios-sdk/Crashlytics/run`),
not a feature mention in the script body, but the underlying anti-pattern
is identical to the `.sh`-routed cases. No change to
`scripts/analyzers/script_phase.py::_is_artifact_upload_extended` —
the existing heuristic correctly surfaces this. Strict FP rate stays
0/26; loose FP rate (counting this hit) stays 1/26 = 3.8%, well under
the 20% gate. The Phase A simulate `script-phase/missing-debug-guard`
predictor's tuning_data_point notes this disposition.

## Phase A simulate prediction tuning data points

Phase A (`ios-build-simulate`) builds a per-rule prediction function on
top of the Phase A finding-level `wall_clock_predicted_seconds` block.
Each predictor cites a tuning data point on both clean and incremental
axes per AGENTS.md non-negotiable principle 5. The tuning points are
encoded in the predictor source under
[`scripts/simulators/`](../scripts/simulators/) and reproduced here as
the human-facing table.

| rule_id | tuning data point (clean) | tuning data point (incremental) |
| --- | --- | --- |
| `script-phase/random-sleep` | REDACTED REDACTED `Step7_RunCrashlytics.sh:13` `sleep $[ ( $RANDOM % 10 ) + 1 ]s` → mean 5.5s, range 1–10s | same — sleep runs unconditionally on every build |
| `script-phase/missing-debug-guard` | REDACTED 4/26 baseline incremental: Step7+Step8 combined ~3s; 1.5s per finding aggregated | same — guard fires regardless of clean/incremental |
| `script-phase/missing-output-declarations` | REDACTED REDACTED step-22 CSV; per-phase 4s ±1; sum capped at sqrt(N)×4 to model post-sandbox+fuse parallel fan-out | same shape; cap applies symmetrically |
| `script-phase/swiftlint-on-build` | REDACTED Step1_SwiftLintCheck heuristic: clean ~3s, incremental ~2s (1–6 range) | REDACTED heuristic: ~2s per finding |
| `build-setting/compilation-cache-disabled` | REDACTED Phase D measurement: 45.6% on warm-cache clean (~125s on 275s baseline); when measurement.json supplies a project baseline, prediction scales accordingly | REDACTED Phase D measured ~10s incremental regression cost (positive value = regression) |
| `build-setting/eager-linking-disabled` | REDACTED Phase v1→v2 measurement: zero clean improvement; predicted 0s ±8 to surface the low-confidence shape; Phase A fix re-measure must refuse on null delta | 0s ±0 — eager linking affects scheduling, not incremental wall-clock |
| `build-setting/script-sandboxing-disabled` (PR-#2) | WWDC22 110364: indirect; estimate=null; wins materialise via `FUSE_BUILD_SCRIPT_PHASES` once enabled | same |
| `build-setting/fuse-build-script-phases-disabled` (PR-#2) | WWDC22 110364: heuristic 0.5s clean / 0.4s incremental per phase × REDACTED 14-phase count → ~7s clean / ~5.6s incremental | same — heuristic, project-shape sensitive |
| `asset-catalog/incremental-recompile` | F5 is incremental-only — predicted 0s clean (asset catalog always compiles cold) | REDACTED Phase A `measurement.json` `incremental.critical_path.nodes` `CompileAssetCatalogVariant`=4.366s; predictor uses literal node duration when supplied, else falls back to that reference |
| `spm/swift-syntax-not-prebuilt` | REDACTED Package.resolved swift-syntax 510.0.3 (transitive); Xcode 26 prebuilt mechanism line-level verified by Phase A S6a; predicted -12s ±7s clean | 0s — F6 is a clean-build finding; swift-syntax compiles once |
| `spm/oversized-module` | REDACTED REDACTED module file counts: REDACTED=794, REDACTED=330; per-file emit ~0.05s clean; 794 × 0.05 ≈ 40s matches Phase A estimate 39.7s | 0s default — incremental cost only materialises on file-level edits inside the module (captured in `applies_when`) |
