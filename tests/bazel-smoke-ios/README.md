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

## Known limitations (deferred to v1.2)

- The chrome-trace JSON profile is captured at
  `<output_dir>/build-<kind>-<repeat>-profile.json` but is not yet
  parsed for per-target critical-path attribution. `measurement.json`
  falls back to `method=null, nodes=[]` with a "no Build Timing Summary
  found" note. v1.2 will add a Bazel profile parser.
- `show_build_settings`, `script_phases`, and `package_graph` for Bazel
  remain `NotImplementedError`; `ios-build-diagnose` short-circuits on
  Bazel projects with a `diagnose-incomplete` note.
