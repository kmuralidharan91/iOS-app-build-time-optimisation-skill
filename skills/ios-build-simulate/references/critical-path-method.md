# `critical_path` method — accuracy bounds

## v1 method (Phase A): `task-class-aggregate`

The Phase A implementation parses the `Build Timing Summary` block emitted by `xcodebuild -showBuildTimingSummary` and reports task-class aggregates ranked by total wall-clock. Each "node" in the artifact's `critical_path.<build_type>.nodes` array is one xcodebuild task class (e.g. `SwiftCompile`, `Ld`, `CompileC`); `longest_chain_seconds` is the duration of the dominant class.

**This is not a true critical-path DAG.** It does not walk per-target dependencies. The Phase A REDACTED smoke produced `SwiftCompile = 2336.1 seconds` as the dominant clean-build class — reflecting cumulative SwiftCompile work across all parallel-compiled targets, not a single longest dependency chain. Two targets that compile in parallel will both contribute to the SwiftCompile total even though only the slower one extends wall-clock.

When using this output, treat the `nodes` array as a **wall-clock budget by task class**: where the time is going, *not* what's blocking what.

## What's deferred to later chats

- **Per-target span attribution.** Requires parsing the 14000+ `ActivityLogCommandInvocationSection` entries inside the `.xcresult` bundle and recovering target names from underlying argv (`-module-name`, `-target`). Verified 2026-05-04 against the Phase A baseline xcresult: `xcrun xcresulttool get --legacy --format json --id <build-log-ref-id>` returns a flat list of command invocations at the top level — no per-target grouping. xcresulttool 24757, schema 0.1.0, legacy commands format 3.58.
- **DAG walk on per-target spans.** Once per-target spans are recovered, a topological-sort + longest-path computation gives the actual critical chain. Implementation drafted in Phase A (see `_longest_chain` in an earlier `critical_path.py` revision; reverted before commit because input data wasn't available).
- **Cross-build-system parity.** Bazel emits a JSON profile via `bazelisk build --profile=<path>` whose flow events expose true per-target spans natively. When the Bazel adapter ships measurement (Phase A or later), its critical-path will be method `bazel-flow-events`, not `task-class-aggregate`.

## Validity bounds (current method)

- Reliable for "where does time go in this build."
- **Unreliable** for "if I made target X faster, would the build wall-clock drop." The dominant task class can be reduced without affecting wall-clock if the slow target happens to be off the critical path — and the current method has no way to tell.
- Numbers are dominated by the most parallel-compiled task class on a real iOS build (SwiftCompile is almost always at the top).

## Update path

This file is updated when the per-target / DAG-walk method ships. The artifact's `critical_path.<build_type>.method` field will then become `xcresult-target-graph` (preferred) with `task-class-aggregate` as the fallback.

## Phase A update — task-class-aggregate is the v1 contract

Phase A (`ios-build-diagnose`) does **not** ship per-target DAG attribution. Per the Phase A plan, per-target spans + DAG walk is its own multi-day workstream and was deferred to keep Phase A focused on the diagnose effectiveness gate. The Phase A `asset-catalog/incremental-recompile` rule reads the `critical_path.incremental.nodes` array exactly as Phase A emits it; the field-name handling tolerates both `dominant_task`/`duration_seconds` (Phase A schema) and `class_name`/`total_seconds` (a hypothetical xcresult-target-graph future schema) so Phase A+ can flip the method without breaking the diagnose rule.

This means:

- `critical_path.<build_type>.method` stays `task-class-aggregate` for v1 (chats 0–6).
- The "shorten this task class to shrink wall-clock" inference is still **wrong** — see "Validity bounds" above.
- Phase A simulate predicts wall-clock Δ on a per-finding basis using `references/defaults.md` reference data, not on the critical-path numbers.
- Per-target DAG attribution lands as a v1.x dedicated workstream once a stable xcresult-target-graph data path (or Bazel-flow-events fallback) is implemented.
