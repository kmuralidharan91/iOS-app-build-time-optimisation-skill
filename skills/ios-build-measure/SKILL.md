---
name: ios-build-measure
description: Benchmark iOS build wall-clock across Xcode, Tuist, and Bazel projects with repeatable inputs, critical-path attribution, variance flagging, and cross-run regression history. Use when the user asks how long a build takes, whether builds got faster or slower over time, or wants a measurement baseline before applying optimisations.
---

# `ios-build-measure`

Measures iOS build wall-clock with repeatable inputs and emits a JSON artifact suitable for downstream diagnose / simulate / fix skills. The same artifact lands in `<project>/.build-history/runs/` so cross-run regressions are flagged automatically.

## When to use

Reach for this skill when the user wants a **measurement** answer — not advice yet. Symptoms that map here:

- "How long does a clean build take?"
- "Did this PR make builds slower?"
- "I want to baseline before I try anything."
- "What part of the build dominates wall-clock?"

If the user is asking *why* the build is slow, prefer `ios-build-diagnose`. If they want a fix applied, prefer `ios-build-fix`. The orchestrator `ios-build-doctor` chains all of them; this skill is the foundation it builds on.

## Inputs

| Argument | Required | Default | Notes |
| --- | --- | --- | --- |
| `--project-path PATH` | no | `cwd` | Project root. Build system auto-detected. |
| `--scheme NAME` | Xcode/Tuist | — | Required for Xcode and Tuist. |
| `--configuration NAME` | no | `Debug` | Free string. REDACTED uses `Distribution` for release-equivalent. |
| `--destination STR` | no | `generic/platform=iOS Simulator` | Forwarded to `xcodebuild -destination`. |
| `--platform STR` | no | `ios` | v1 enforces `ios`; v2 adds macOS / watchOS / tvOS / visionOS. |
| `--repeats N` | no | `3` | Repeats per build type. ≥5 recommended when variance is high. |
| `--build-types LIST` | no | `clean,incremental` | Comma-separated; `clean` and/or `incremental`. |
| `--touch-file PATH` | when `incremental` is in `--build-types` | — | File to `touch` between incremental repeats. |
| `--variance-threshold N` | no | `10.0` | Spread-as-percent-of-median above which `high_variance` fires. |
| `--regression-window N` | no | `5` | History runs (same branch) compared against. |
| `--output-dir DIR` | yes | — | Where `benchmark.json`, per-run logs, and `.xcresult` bundles go. |
| `--extra-xcodebuild-arg ARG` | no | — | Repeatable; forwarded verbatim to `xcodebuild`. |

## Workflow

1. **Detect** the build system at `--project-path`. The detector looks for `Project.swift` (Tuist) → `MODULE.bazel` / `WORKSPACE` plus `BUILD` files (Bazel) → `*.xcodeproj` / `*.xcworkspace` (Xcode), with tie-breaker order Tuist > Bazel > Xcode (per `AGENTS.md`).
2. **Run** `--repeats` builds per requested kind. Clean runs invoke `xcodebuild clean` then `xcodebuild build -showBuildTimingSummary -resultBundlePath …`. Incremental runs `touch` `--touch-file` then build without cleaning. Each run is wall-clock-timed with `time.monotonic`.
3. **Aggregate** per-build-type summaries: `min_seconds`, `max_seconds`, `median_seconds`, `mean_seconds`, `spread_seconds`, `spread_percent`. `high_variance` fires when `spread_percent` exceeds `--variance-threshold` and `count ≥ 2`.
4. **Derive** `critical_path` per build type from the most recent run's stdout log (preferred) or `.xcresult` bundle (fallback). Phase A ships the task-class-aggregate method; per-target DAG attribution lands in Phase A.
5. **Compare** against `<project>/.build-history/runs/` for the same branch; flag regression when current median exceeds historical median-of-medians by more than `--variance-threshold` percent on any build type.
6. **Persist** the artifact to `--output-dir/benchmark.json` AND to `<project>/.build-history/runs/<timestamp>__sha-<sha>__<scheme>-<config>.json`.

When `high_variance` fires AND `--repeats < 5`, a Warning is emitted to stderr and appended to the artifact's `notes` array recommending `--repeats=5`.

## Outputs

A single JSON artifact conforming to [`schemas/build-benchmark.schema.json`](../../schemas/build-benchmark.schema.json). The cross-run history shape is in [`schemas/history.schema.json`](../../schemas/history.schema.json).

Top-level fields:

- `schema_version` — `"1.0.0"`.
- `tool` — `{name, version}` of the producer.
- `generated_at` — ISO-8601 UTC timestamp.
- `project` — path, detected build system, git SHA / branch, platform.
- `configuration` — scheme, configuration, destination, repeats, build types, variance threshold, extra args.
- `runs.<build_type>` — array of `TimedBuild` records.
- `summary.<build_type>` — `TypeSummary` rollup.
- `critical_path.<build_type>` — method + ranked task-class nodes + longest-chain seconds + notes.
- `history` — regression report (deltas, window, threshold, notes).
- `notes` — high-variance Warnings and other call-outs.

## Failure modes (what this skill refuses to do)

- **No project mutation.** This skill never edits source files, project.pbxproj, or build settings. Only `--touch-file` is touched, and only when an incremental measurement is requested.
- **No silent abort on a failed build.** A non-zero `xcodebuild` exit is recorded in the run's `exit_code` field; the benchmark continues so the user sees a measurable baseline even when one repeat fails. Downstream consumers should ignore failed runs when computing summaries.
- **No spurious regression flags.** Regression detection requires at least one historical run on the same branch; the first run on a new branch reports `window_used=0` and `regression_detected=false`.
- **No platform fudge.** `--platform` other than `ios` raises `ValueError`; v2 work is additive, not retrofitted into v1 measurements.

## References

- Apple [`xcodebuild` man page mirror](https://keith.github.io/xcode-man-pages/xcodebuild.1.html) for `-showBuildTimingSummary` / `-resultBundlePath`.
- Apple [Build System overview](https://developer.apple.com/documentation/xcode/build-system).
- [Tuist manifests guide](https://docs.tuist.dev/en/guides/features/projects/manifests) — `Project.swift` detection rule.
- [Bazel and Apple](https://bazel.build/docs/bazel-and-apple) + [`rules_apple`](https://github.com/bazelbuild/rules_apple) — `MODULE.bazel` / `WORKSPACE` + `BUILD` detection rule.
- This skill bundles its own copy of `scripts/` and `schemas/`. Verify drift with `python3 scripts/verify-sync.py` from the repo root.
