# bazel-smoke-ios

Bazel iOS smoke target. Used to exercise the full `ios-build-doctor`
pipeline (measure → diagnose → simulate → fix) against a Bazel-backed
build end-to-end.

## Layout

```
tests/bazel-smoke-ios/
├── App/
│   ├── BUILD.bazel       — VersionStamp (clean) + LintAndStamp (fixture) genrules
│   ├── Counter.swift, Formatter.swift, Greeter.swift
│   └── (swift_library SmokeLib)
├── Lib/
│   ├── BUILD.bazel       — second swift_library MathKit depending on SmokeLib
│   ├── Adder.swift, Multiplier.swift
├── Packages/LocalPkg/
│   ├── Package.swift     — local SPM module (fixture-only, not in Bazel build)
│   ├── Package.resolved  — pins swift-syntax 510.0.3 so F6 fires
│   └── Sources/LocalPkg/LocalPkg.swift
├── MODULE.bazel          — pins rules_swift 3.6.1, rules_apple 4.5.3, etc.
├── .bazelrc              — `--config=ios_sim` for iOS simulator builds
└── .bazelversion         — 9.1.0
```

Two genrules live in `App/BUILD.bazel`:

- **`VersionStamp`** (well-behaved) — declared `outs`, simple `date` cmd.
  The analyzer should produce zero findings against this one.
- **`LintAndStamp`** (deliberate fixture) — `cmd` contains
  `sleep $$RANDOM`, `swiftlint`, and `outs = []`. Triggers F1
  (random-sleep), F3 (missing-output-declarations), and F8
  (swiftlint-on-build). Never built by the main target — bazel query
  reads it without running analysis, so the empty-outs declaration is
  harmless.

The local SPM package at `Packages/LocalPkg/` declares a `swift-syntax`
pin in `Package.resolved` so the `bazel_adapter.package_graph()` +
`spm_graph` analyzer combination fires F6
(spm/swift-syntax-not-prebuilt) on a Bazel project — proving the
SPM-side diagnose rules work identically across Xcode/Bazel/Tuist.

## Reproduce the v1.3 verification numbers

From the repo root:

```bash
# Provision Bazelisk via Homebrew (one-time)
brew install bazelisk

# Clean — measures the full //Lib:MathKit DAG (depends on //App:SmokeLib)
python3 scripts/benchmark.py \
    --project-path tests/bazel-smoke-ios \
    --scheme "//Lib:MathKit" \
    --configuration ios_sim \
    --destination "" \
    --build-types clean \
    --repeats 3 \
    --output-dir /tmp/bazel-smoke-clean

# Incremental — touch a source file between repeats
python3 scripts/benchmark.py \
    --project-path tests/bazel-smoke-ios \
    --scheme "//Lib:MathKit" \
    --configuration ios_sim \
    --destination "" \
    --build-types incremental \
    --touch-file tests/bazel-smoke-ios/Lib/Multiplier.swift \
    --repeats 3 \
    --output-dir /tmp/bazel-smoke-incr

# End-to-end doctor (measure → diagnose → simulate)
python3 scripts/doctor.py \
    --project-path tests/bazel-smoke-ios \
    --scheme "//Lib:MathKit" \
    --configuration ios_sim \
    --destination "" \
    --build-types clean \
    --repeats 1 \
    --goal find \
    --non-interactive \
    --output-dir /tmp/bazel-smoke-doctor

# Verify a Bazel-aware fixer returns an honest informational stub
python3 scripts/doctor.py \
    --project-path tests/bazel-smoke-ios \
    --scheme "//Lib:MathKit" \
    --configuration ios_sim \
    --destination "" \
    --build-types clean \
    --repeats 1 \
    --goal apply \
    --rule-id script-phase/random-sleep \
    --non-interactive \
    --auto-approve-fix \
    --worktree-seed-ref feat/bazel-v1.3 \
    --output-dir /tmp/bazel-smoke-apply
```

## Prereqs

- Bazelisk on `PATH` (`brew install bazelisk`). The adapter prefers
  `bazelisk` over a bare `bazel` binary because Bazelisk auto-resolves
  the version pinned in `.bazelversion` (currently 9.1.0).
- Xcode and the iOS simulator SDK matching `--ios_minimum_os=17.6` in
  `.bazelrc`.
- `apple_support` >= 2.5.0 (pinned via `MODULE.bazel`); older versions
  ship `wrapped_clang_pp` binaries that hit a macOS 26 Gatekeeper kill
  for adhoc signatures.

## v1.3 verification (this project)

Measured 2026-05-17 against Xcode 26.5 / Bazel 9.1.0 / rules_swift
3.6.1 / rules_apple 4.5.3 on Apple Silicon, 3 repeats per axis (scheme
`//Lib:MathKit`):

- Measurement: clean median **19.898 s** (spread 12.04 %), incremental
  median **0.136 s** (spread 111.77 % — tiny absolute values blow up
  the percentage; bump `--repeats 5` if the noise matters).
- Critical path: 2 nodes from `cat="critical path component"`, method
  `bazel-critical-path`, longest chain ≈ **9.75 s**.
- Diagnose findings (4 total, all on Bazel project):
  - `script-phase/random-sleep` (F1, high) — `LintAndStamp` cmd
  - `script-phase/missing-output-declarations` (F3, medium) —
    `LintAndStamp` outs=[]
  - `script-phase/swiftlint-on-build` (F8, low) — `LintAndStamp` cmd
  - `spm/swift-syntax-not-prebuilt` (F6, medium) —
    `Packages/LocalPkg/Package.resolved`
- F4 (compilation-cache-disabled), F9 (eager-linking-disabled),
  ENABLE_USER_SCRIPT_SANDBOXING, FUSE_BUILD_SCRIPT_PHASES are
  **correctly suppressed** on Bazel (these are Xcode-only settings; no
  Bazel analogue exists).
- Bazel-aware F1 / F3 fixer behaviour: returns `applied_fix.kind="no-op"`
  with outcome `refused-null` and a manual recipe in the preview. The
  buildozer-backed Bazel auto-apply ships in v1.4.

## Known limitations (deferred to v1.4)

- **F5 (asset-catalog/incremental-recompile)** keys on the Xcode-specific
  task class `CompileAssetCatalogVariant`. Bazel chrome-trace uses
  different action names (e.g. `AppleAssetCatalog`), so F5 is a false
  negative on Bazel. v1.4 will add a Bazel-aware action-name matcher.
- **Auto-apply Bazel fixers** (F1 / F3 cmd rewriters) require Starlark
  AST manipulation (via `buildozer`). v1.3 ships the informational stub
  with a manual recipe; v1.4 will ship the buildozer-backed apply.
- **wikipedia-ios-bazel** real-corpus measurement remains paused at the
  WMF Framework Swift↔Obj-C interop cycle — an architectural refactor
  in the upstream codebase (splitting Obj-C into pre-Swift and
  post-Swift layers) rather than Bazel-rule work.
