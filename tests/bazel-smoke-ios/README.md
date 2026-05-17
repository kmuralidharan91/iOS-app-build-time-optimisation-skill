# bazel-smoke-ios

Minimal Bazel iOS project used to smoke-test `bazel_adapter.measure()`.

The library has three Swift sources (`Greeter`, `Counter`, `Formatter`)
compiled by `swift_library` into `libSmokeLib.a`. No app target, no
extensions, no resources — the goal is to exercise the adapter end-to-end
in seconds, not to demonstrate a realistic project.

## Reproduce the v1.1 verification numbers

From the repo root:

```bash
# Clean — one repeat for a fast smoke; bump --repeats for noise control
python3 scripts/benchmark.py \
    --project-path tests/bazel-smoke-ios \
    --scheme "//App:SmokeLib" \
    --configuration ios_sim \
    --destination "" \
    --build-types clean \
    --repeats 1 \
    --output-dir /tmp/bazel-smoke-clean

# Incremental — touch a source file between repeats
python3 scripts/benchmark.py \
    --project-path tests/bazel-smoke-ios \
    --scheme "//App:SmokeLib" \
    --configuration ios_sim \
    --destination "" \
    --build-types incremental \
    --touch-file tests/bazel-smoke-ios/App/Counter.swift \
    --repeats 1 \
    --output-dir /tmp/bazel-smoke-incr

# End-to-end doctor (should produce a transcript without firing the v1 fence)
python3 scripts/doctor.py \
    --project-path tests/bazel-smoke-ios \
    --scheme "//App:SmokeLib" \
    --configuration ios_sim \
    --build-types clean \
    --skip-questionnaire \
    --skip-xcodebuild \
    --output-dir /tmp/bazel-smoke-doctor
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

## Known limitations (deferred to v1.3)

- The upstream rule catalog (F1–F9) is calibrated on Xcode build
  settings. Running `ios-build-diagnose` on a Bazel project produces a
  mix of valid findings (rules keyed on build-system-agnostic
  attributes like `alwayslink`) and spurious findings (rules keyed on
  Xcode-only settings like `COMPILATION_CACHE_ENABLE_CACHING`). v1.3
  adds Bazel-specific rule variants and adjusts the catalog.
- `fix.py` does not ship Bazel-specific fixers; the upstream
  Xcode-side rewriters will return `refused-apply-error` against a
  Bazel project. Bazel fixers (genrule rewriters, `MODULE.bazel`
  patch-and-rebuild) are deferred to v1.3.

## v1.2 verification (this project)

- Measurement: clean 21.153 s, incremental 0.298 s.
- Critical path: 2 nodes from `cat="critical path component"`, method=`bazel-critical-path`, longest_chain ≈ 9.6 s.
- script_phases: 1 (the `VersionStamp` genrule), `always_out_of_date=False`.
- package_graph: 0 pins, 0 local modules (the smoke target ships no SPM dependencies).
- show_build_settings: 11 keys (7 from `bazel info`, 4 from `bazel cquery`).
