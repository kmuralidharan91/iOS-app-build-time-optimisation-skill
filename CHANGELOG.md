# Changelog

All notable changes to this project. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] — 2026-05-17

### Added

- **Tuist end-to-end.** `tuist_adapter` implements `measure()`,
  `show_build_settings()`, `script_phases()`, and `package_graph()`.
  `measure()` runs `tuist generate --no-open` (auto-resolved via
  `mise exec -- tuist` when no bare `tuist` binary is on PATH) then
  delegates the timed build to `xcode_adapter.measure()` against the
  generated `*.xcworkspace`; the other three surfaces delegate to
  `xcode_adapter` directly because Tuist generates a real Xcode
  workspace with stock `project.pbxproj`, `Package.resolved`, and
  `Package.swift` artefacts that the existing parsers handle
  unchanged. Verified against `tests/tuist-smoke-ios/` (Tuist 4.191.5,
  Xcode 26.5): clean median 2.258 s, incremental 1.811 s; diagnose
  fires 2 findings (F4, F9) + 2 recommendations.
- **`ios-build-doctor` v1 fence dropped for Tuist.** All three build
  systems (Xcode, Bazel, Tuist) now run the full
  questionnaire → measure → diagnose → simulate → fix loop. The
  `abort:tuist-v1-fence` outcome string is gone; `_exit_code_for()`
  no longer special-cases it. Detection unchanged.
- **F1–F9 calibrated for Bazel.** The `build_setting` analyzer now
  short-circuits cleanly when `context.build_system == "bazel"`:
  F4 (`COMPILATION_CACHE_ENABLE_CACHING`), F9 (`EAGER_LINKING`),
  ENABLE_USER_SCRIPT_SANDBOXING, and FUSE_BUILD_SCRIPT_PHASES all key
  off Xcode-specific build settings with no Bazel analogue. v1.2 fired
  all four spuriously on Bazel projects; v1.3 emits zero findings for
  them. F1 (script-phase/random-sleep), F3 (script-phase/
  missing-output-declarations), F8 (script-phase/swiftlint-on-build),
  F6 (spm/swift-syntax-not-prebuilt), and F7 (spm/oversized-module) are
  build-system-agnostic by design (they consume `ScriptPhase` /
  `PackageGraph` dataclasses which `bazel_adapter` populates from
  `bazel query` and the project tree) and fire identically on Bazel
  projects. Verified against the enhanced
  `tests/bazel-smoke-ios/`: F1+F3+F8 fire on the `LintAndStamp`
  genrule fixture; F6 fires on the `LocalPkg` Package.resolved
  fixture; F4/F9/sandboxing/fuse stay quiet.
- **Bazel-aware fixers via informational stubs.** `apply_random_sleep`
  (F1) and `apply_missing_output_declarations` (F3) dispatch on
  `ctx.diagnosis["project"]["build_system"]`. The Bazel branch returns
  a no-op `AppliedFix` plus a manual recipe in `preview_*`. Outcome is
  `refused-null` per the existing fix.py informational-fixer contract.
  Auto-applying BUILD.bazel rewriters requires Starlark AST
  manipulation (e.g. buildozer); v1.4 ships the auto-apply.
- **`DiagnosisContext.build_system` field.** The diagnose context now
  carries the detected build system so analyzers can do build-system-
  aware filtering. Diagnosis artefact's `project.build_system` is now
  the actual detection result (was hardcoded `"xcode"` in v1.2).
- **`tests/tuist-smoke-ios/`.** Minimal Tuist iOS smoke target
  (Project.swift + 3 Swift sources, mise-pinned Tuist 4.191.5). Used
  to verify the Tuist measurement loop end-to-end without depending on
  a third-party Tuist project.
- **Enhanced `tests/bazel-smoke-ios/`.** Added `Lib/MathKit`
  (second `swift_library` depending on `SmokeLib`, gives the critical
  path a 2-node chain), `App/LintAndStamp` (deliberately-broken
  genrule fixture that triggers F1/F3/F8), and `Packages/LocalPkg/`
  (local SPM package with a `Package.resolved` pinning swift-syntax
  510.0.3 so F6 fires).
- **`mise exec` fallback for Tuist resolution.** The Tuist adapter
  prefers a bare `tuist` on PATH but falls back to
  `["mise", "exec", "--", "tuist"]` so a project's `.mise.toml`
  pinned version is honoured automatically.

### Known limitations (deferred to v1.4)

- **F5 (asset-catalog/incremental-recompile)** keys on the Xcode-
  specific task class name `CompileAssetCatalogVariant`. Bazel
  chrome-trace events use different action names (e.g.
  `AppleAssetCatalog`); v1.3 doesn't add a Bazel matcher, so F5 is a
  false-negative on Bazel projects (no spurious fires; just doesn't
  catch the case).
- **BUILD.bazel auto-apply for F1 / F3.** Informational stubs ship in
  v1.3 with manual recipes in `preview_*`. v1.4 will ship buildozer-
  backed apply functions that mutate the `cmd` and `outs` attributes
  in-place.
- **wikipedia-ios-bazel real-corpus measurement** remains paused at
  the WMF Framework Swift↔Obj-C interop cycle. The blocker is
  architectural (the upstream codebase mixes Obj-C base types and
  Swift-derived types in the same Bazel module, creating a circular
  module-map dependency that requires splitting the WMF target into
  pre-Swift and post-Swift halves). This is upstream-codebase work
  rather than skill-side rule work and is deferred to v1.4.

### Verification corpus

- `tests/tuist-smoke-ios/` — Tuist 4.191.5 / Xcode 26.5 / 3 reps:
  clean 2.258 s, incremental 1.811 s. Diagnose: 2 findings + 2 recs.
- `tests/bazel-smoke-ios/` (enhanced) — Bazel 9.1.0 / rules_swift
  3.6.1 / 3 reps: clean 19.898 s (spread 12.04 %), incremental 0.136 s.
  Diagnose: 4 findings (F1+F3+F6+F8), 0 spurious recs.
- All three build systems pass `python3 scripts/doctor.py` end-to-end
  with no `abort:*` outcomes.

## [1.2.0] — 2026-05-16

### Added

- **Bazel critical-path attribution from the chrome-trace profile.**
  `scripts/critical_path.py` parses the JSON profile that
  `bazelisk build --profile=<json>` writes alongside the stdout log.
  Prefers events with `cat == "critical path component"` (Bazel's
  server-side critical-path output, ordered by the action DAG); falls
  back to top `cat == "action processing"` events ranked by wall-clock
  on tiny builds. Method names: `bazel-critical-path` (preferred) and
  `bazel-action-ranked` (fallback). Verified against
  `tests/bazel-smoke-ios/`: clean run → 2 critical-path nodes with
  `longest_chain_seconds ≈ 9.6 s`.
- **`bazel_adapter.script_phases()` implementation.** Wraps
  `bazel query 'kind(genrule, //...)' --output=xml`, parses the result,
  and exposes each genrule as a `ScriptPhase` (target, name, script
  body, srcs, outs, always_out_of_date flag, BUILD.bazel:line:col
  location). `always_out_of_date` fires when the genrule tags include
  `no-cache`/`no-remote-cache`/`local` OR when it declares no outs.
  Verified against the smoke target's `//App:VersionStamp` genrule.
- **`bazel_adapter.package_graph()` implementation.** Walks the project
  tree for `Package.resolved` files (workspace-level + per-package),
  extracts pins (SPM v2 and v3 schemas), and walks `Package.swift`
  manifests for local module Swift-file counts. Verified against
  `wikipedia-ios-bazel`: 10 pins + 7 local modules.
- **`bazel_adapter.show_build_settings()` implementation.** Combines
  `bazel info` (workspace facts: release, execution_root, output_path,
  etc.) with `bazel cquery <target> --output=jsonproto` (per-target
  attributes that matter for the v1 rules: compilation_mode, copts,
  swiftcopts, features, module_name, alwayslink, enable_modules,
  generates_header, library_evolution). Keys are namespaced (`bazel.info.*`,
  `bazel.target.*`) so the upstream build_setting analyzer can tell
  them apart from xcodebuild keys.
- **`ios-build-diagnose` now runs end-to-end on Bazel projects.**
  Routes `build_system == "bazel"` through the three adapter surfaces
  above. Honours `--skip-xcodebuild` (renamed semantically to "skip
  build-system invocation") and `--resolved-settings-json`. The
  analyzers themselves are build-system-agnostic — they consume the
  dataclasses defined in `adapters/__init__.py` — so once the adapter
  returns the same shapes, rules fire identically.

### Known limitations (deferred to v1.3)

- The upstream rules (F1–F9) are calibrated on Xcode build settings;
  on a Bazel project they will produce a mix of valid findings (when
  the rule keys off a build-system-agnostic attribute like
  `alwayslink`) and spurious findings (when it keys off an
  Xcode-only setting like `COMPILATION_CACHE_ENABLE_CACHING`). v1.3
  will add Bazel-specific rule variants and adjust the rule catalog
  accordingly.
- Bazel-specific fixers are still out of scope for `fix.py`.
  `--rule-id` on `doctor.py --goal apply` against a Bazel project
  still returns `refused-apply-error` for Xcode-only fixers.

## [1.1.0] — 2026-05-16

### Added

- **Bazel measurement adapter ships end-to-end.** `bazel_adapter.measure()` now
  wraps `bazelisk build --profile=<path>` (chrome-trace JSON profile capture)
  with `time.monotonic` wall-clock and returns a `TimedBuild` shaped identically
  to the xcode adapter's output. Verified against a synthetic Bazel iOS smoke
  target (`SmokeLib` Swift library, 3 sources): clean median 21.153 s,
  incremental median 0.298 s after `touch_file.touch()`. The
  `--scheme` CLI flag accepts a Bazel target label (e.g. `//App:SmokeLib`);
  `--configuration` maps to `bazelisk --config=<name>`; `--destination` is
  xcodebuild-specific and is ignored with a logged note.
- **`ios-build-doctor` v1 fence relaxed for Bazel.** `detect_build_system()`
  returning `"bazel"` no longer fires the fence. The outcome string renamed
  from `abort:non-xcode-v1-fence` to `abort:tuist-v1-fence`; the fence still
  fires for Tuist (full Tuist end-to-end is deferred to v1.x once a Tuist-shaped
  smoke target lands).
- **`ios-build-diagnose` short-circuits gracefully on Bazel.** Returns an empty
  `DiagnosisContext` plus a `diagnose-incomplete` note explaining that
  BUILD-file script-phase analysis, `bazel query --output=build` resolved
  settings, and package-graph extraction from `rules_swift_package_manager`
  pins are deferred to v1.x.

### Verification corpus

- `tests/bazel-smoke-ios/` — minimal Bazel iOS smoke target. Reproduce with:
  `python3 scripts/benchmark.py --project-path tests/bazel-smoke-ios --scheme //App:SmokeLib --configuration ios_sim --destination "" --build-types clean,incremental --touch-file tests/bazel-smoke-ios/App/Counter.swift --repeats 3 --output-dir /tmp/bazel-out/`.

### Deferred to v1.2

- Bazel-side critical-path attribution from the chrome-trace JSON profile
  (per-target wall-clock DAG via `traceEvents[]` flow events). v1.1 captures
  the profile JSON alongside the stdout log but does not yet parse it.
- Bazel measured benchmarks on wikipedia-ios: paused at WMFData
  `apple_core_data_model` (resolved 2026-05-16, see
  `bazel/progress.md`) plus Swift→Obj-C module-map work for the legacy WMF
  Framework target. Telegram-iOS remains the eventual large-corpus smoke
  target once an Xcode-26.4-pinned environment is available.
- Bazel diagnose (`show_build_settings`, `script_phases`, `package_graph`).
- Tuist end-to-end (`tuist_adapter.measure()`).

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
    +2.92 s; reproduce by running `ios-build-fix` with rule
    `script-phase/missing-output-declarations` against
    [NetNewsWire](https://github.com/Ranchero-Software/NetNewsWire)@`build-comparison-base`).
  - F4 (compilation-cache): `refused-noise` (clean −1.32 s within
    variance, incremental +10.86 s expected regression; reproduce by
    running `ios-build-fix` with rule
    `build-setting/compilation-cache-disabled` against
    [NetNewsWire](https://github.com/Ranchero-Software/NetNewsWire)@`build-comparison-base`).
  - F9 (eager-linking, designed null-delta): `refused-regressive`
    (clean +3.47 s, incremental +2.06 s; reproduce by running
    `ios-build-fix` with rule `build-setting/eager-linking-disabled`
    against [NetNewsWire](https://github.com/Ranchero-Software/NetNewsWire)@`build-comparison-base`).

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
