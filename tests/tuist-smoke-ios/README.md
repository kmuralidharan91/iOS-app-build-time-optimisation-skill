# tuist-smoke-ios

Minimal Tuist iOS project used to smoke-test `tuist_adapter.measure()`
and the diagnose surface that delegates through it.

The framework target has three Swift sources (`Greeter`, `Counter`,
`Formatter`) compiled into `TuistSmoke.framework`. No app target, no
extensions, no resources — the goal is to exercise the adapter
end-to-end in seconds, not to demonstrate a realistic project.

## Reproduce the v1.3 verification numbers

From the repo root:

```bash
# Provision Tuist via mise (first time only — pinned to 4.191.5)
cd tests/tuist-smoke-ios && mise install && cd ../..

# Clean — one repeat for a fast smoke; bump --repeats for noise control
python3 scripts/benchmark.py \
    --project-path tests/tuist-smoke-ios \
    --scheme TuistSmoke \
    --configuration Debug \
    --destination "generic/platform=iOS Simulator" \
    --build-types clean \
    --repeats 1 \
    --output-dir /tmp/tuist-smoke-clean

# Incremental — touch a source file between repeats
python3 scripts/benchmark.py \
    --project-path tests/tuist-smoke-ios \
    --scheme TuistSmoke \
    --configuration Debug \
    --destination "generic/platform=iOS Simulator" \
    --build-types incremental \
    --touch-file tests/tuist-smoke-ios/Sources/Counter.swift \
    --repeats 1 \
    --output-dir /tmp/tuist-smoke-incr

# End-to-end doctor (should run measure -> diagnose -> simulate)
python3 scripts/doctor.py \
    --project-path tests/tuist-smoke-ios \
    --scheme TuistSmoke \
    --configuration Debug \
    --build-types clean \
    --repeats 1 \
    --goal find \
    --non-interactive \
    --output-dir /tmp/tuist-smoke-doctor
```

## Prereqs

- `mise` on PATH (`brew install mise`). `tuist_adapter` falls back to
  `mise exec -- tuist` when a bare `tuist` binary is not on PATH; mise
  resolves the version pinned in `.mise.toml` (currently 4.191.5).
- Xcode and the iOS simulator SDK matching the
  `deploymentTargets: .iOS("17.0")` line in `Project.swift`.
- `xcodebuild` on PATH — `tuist generate` produces a stock
  `*.xcworkspace` and `tuist_adapter.measure()` delegates the actual
  build invocation to `xcode_adapter` against that workspace.

## Known limitations (deferred to later releases)

- The smoke target ships no SPM dependencies and no script phases, so
  F1 / F3 / F6 / F7 / F8 don't fire here — those rules need a fixture
  with at least one SwiftLint-style genrule, one large local module,
  or one transitive Package.resolved pin. v1.3 ships F1/F3/F8 against
  the Bazel smoke target's deliberately-broken twin genrule (see
  `tests/bazel-smoke-ios/App/BUILD.bazel`).
- Tuist's manifest DSL (`Project.swift`) is parsed indirectly: this
  adapter runs `tuist generate` and then inspects the generated
  `project.pbxproj`. A future v1.x could parse `Project.swift`
  directly via Tuist's `tuist edit --permanent` AST, but the
  generated-pbxproj path catches everything that compiles down to
  Xcode shapes today.

## v1.3 verification (this project)

Measured 2026-05-16 against Xcode 26.5 / Tuist 4.191.5 on Apple
Silicon (M-series), 3 repeats per axis:

- Measurement: clean median **2.258 s** (spread 8.857 %), incremental
  median **1.811 s** (spread 3.037 %).
- Critical path: top node `SwiftCompile` (0.993 s), method
  `task-class-aggregate` (Tuist delegates to xcodebuild, so the
  critical path comes from the xcresult timeline parser inherited
  from `xcode_adapter`).
- Diagnose surface: 2 findings (F4 + F9 universal-miss on a default
  Tuist project), 2 additional recommendations. `script_phases = []`
  and `package_graph.pins = 0` because the smoke target has neither.
- Simulation: 4 predictions totalling −8.0 s predicted clean Δ on the
  2.258 s baseline (refusal-ready because the predicted delta
  exceeds the median — `fix.py` would correctly mark these
  `refused-noise` when actually applied).
