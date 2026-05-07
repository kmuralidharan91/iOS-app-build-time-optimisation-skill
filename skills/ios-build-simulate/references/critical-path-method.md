# `critical_path` method — accuracy bounds

## v1 method: `task-class-aggregate`

The benchmark implementation parses the `Build Timing Summary` block emitted by `xcodebuild -showBuildTimingSummary` and reports task-class aggregates ranked by total wall-clock. Each "node" in the artifact's `critical_path.<build_type>.nodes` array is one xcodebuild task class (e.g. `SwiftCompile`, `Ld`, `CompileC`); `longest_chain_seconds` is the duration of the dominant class.

**This is not a true critical-path DAG.** It does not walk per-target dependencies. On a private-corpus baseline, `SwiftCompile` totalled ~2336s as the dominant clean-build class — reflecting cumulative SwiftCompile work across all parallel-compiled targets, not a single longest dependency chain. Two targets that compile in parallel will both contribute to the SwiftCompile total even though only the slower one extends wall-clock. TODO(public-cite: NetNewsWire) record the equivalent dominant-class total on the public-cite project.

When using this output, treat the `nodes` array as a **wall-clock budget by task class**: where the time is going, *not* what's blocking what.

## What's deferred to later releases

- **Per-target span attribution.** Requires parsing the 14000+ `ActivityLogCommandInvocationSection` entries inside the `.xcresult` bundle and recovering target names from underlying argv (`-module-name`, `-target`). Verified 2026-05-04 against a baseline xcresult: `xcrun xcresulttool get --legacy --format json --id <build-log-ref-id>` returns a flat list of command invocations at the top level — no per-target grouping. xcresulttool 24757, schema 0.1.0, legacy commands format 3.58.
- **DAG walk on per-target spans.** Once per-target spans are recovered, a topological-sort + longest-path computation gives the actual critical chain.
- **Cross-build-system parity.** Bazel emits a JSON profile via `bazelisk build --profile=<path>` whose flow events expose true per-target spans natively. When the Bazel adapter ships measurement, its critical-path method will be `bazel-flow-events`, not `task-class-aggregate`.

## Validity bounds (current method)

- Reliable for "where does time go in this build."
- **Unreliable** for "if I made target X faster, would the build wall-clock drop." The dominant task class can be reduced without affecting wall-clock if the slow target happens to be off the critical path — and the current method has no way to tell.
- Numbers are dominated by the most parallel-compiled task class on a real iOS build (SwiftCompile is almost always at the top).

## Update path

This file is updated when the per-target / DAG-walk method ships. The artifact's `critical_path.<build_type>.method` field will then become `xcresult-target-graph` (preferred) with `task-class-aggregate` as the fallback.

## Diagnose contract — task-class-aggregate is the v1 contract

`ios-build-diagnose` does **not** ship per-target DAG attribution; per-target spans + DAG walk is its own multi-day workstream. The `asset-catalog/incremental-recompile` rule reads the `critical_path.incremental.nodes` array exactly as benchmark emits it; the field-name handling tolerates both `dominant_task`/`duration_seconds` (current schema) and `class_name`/`total_seconds` (a hypothetical xcresult-target-graph future schema) so a future release can flip the method without breaking the diagnose rule.

This means:

- `critical_path.<build_type>.method` stays `task-class-aggregate` for v1.
- The "shorten this task class to shrink wall-clock" inference is still **wrong** — see "Validity bounds" above.
- Simulate predicts wall-clock Δ on a per-finding basis using `references/defaults.md` reference data, not on the critical-path numbers.
- Per-target DAG attribution lands as a v1.x dedicated workstream once a stable xcresult-target-graph data path (or Bazel-flow-events fallback) is implemented.
