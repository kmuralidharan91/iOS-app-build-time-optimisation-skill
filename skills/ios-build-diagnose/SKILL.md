---
name: ios-build-diagnose
description: Surface ranked iOS build-time findings (script phases, build settings, asset catalogs, SPM graph) each with a rule id, evidence, wall-clock impact category, and an Apple/WWDC/Tuist/Bazel citation. Reads the measurement artifact from ios-build-measure and the on-disk project state. Use when the user asks why a build is slow, wants a list of recommendations to apply, or needs a recommend-first audit before deciding what to fix. Recommend-first; this skill never edits source files. PR-#1 effective-settings logic and PR-#2 sandboxing/fuse audit are baked in.
---

# `ios-build-diagnose`

Reads on-disk project state plus a benchmark `measurement.json` and emits a ranked JSON artifact of findings. Each finding ties to a rule id, evidence (file:line, build-setting key, or measurement node), a wall-clock impact category, and a citation back to Apple / WWDC. Two suite value-add recommendations (sandboxing + fuse, PR-#2) ship in `additional_recommendations[]` so the F1–F9 ground-truth recall denominator stays clean.

## When to use

Reach for this skill when the user wants the **why** answer, not measurement and not a fix:

- "Why is this build slow?"
- "Give me a list of things to try."
- "Audit this project — what's wasted time?"
- "I have a measurement; rank the issues by impact."

If the user wants the time numbers themselves, use [`ios-build-measure`](../ios-build-measure/SKILL.md). If they want a fix applied + verified, use `ios-build-fix`. The orchestrator `ios-build-doctor` chains all three; this skill is the audit step.

## Inputs

| Argument | Required | Default | Notes |
| --- | --- | --- | --- |
| `--project-path PATH` | yes | — | Project root containing `*.xcodeproj` and/or `*.xcworkspace`. |
| `--scheme NAME` | no | — | Required when `xcodebuild -showBuildSettings` is invoked (default flow). |
| `--configuration NAME` | no | `Debug` | Free string. Some projects use `Distribution` for release-equivalent. |
| `--destination STR` | no | `generic/platform=iOS Simulator` | Recorded into the artifact for traceability. |
| `--platform STR` | no | `ios` | v1 enforces `ios`; v2 adds macOS / watchOS / tvOS / visionOS. |
| `--measurement-artifact PATH` | no | — | Path to a `measurement.json` (`ios-build-measure` output). F5 (asset-catalog/incremental-recompile) requires it. |
| `--output-dir DIR` | yes | — | Where `diagnosis.json` is written. |
| `--skip-xcodebuild` | no | `false` | Skip the `xcodebuild -showBuildSettings -json` call (offline / no-VPN). F4, F9, sandboxing, fuse rules short-circuit; pbxproj + SPM rules still run. |
| `--resolved-settings-json PATH` | no | — | Pre-captured `xcodebuild -showBuildSettings -json` dump. When set, the live xcodebuild call is skipped and the dump is used instead. Useful for offline runs and pinned-baseline reproducibility. |

## Workflow

1. **Detect** the build system at `--project-path`. v1 ships diagnose only for the Xcode adapter; Tuist + Bazel adapters' diagnose surface raises `NotImplementedError` until v1.x. The detector is shared with `ios-build-measure` (`scripts/adapters/__init__.py`).
2. **Load** the measurement artifact (when supplied). F5 reads the incremental `critical_path` for `CompileAssetCatalogVariant`; if no artifact is supplied, F5 short-circuits with a top-level note.
3. **Resolve build settings** via `xcodebuild -showBuildSettings -json` (PR-#1 effective-settings semantics: explicit pbxproj value → audit literally; unset + xcodebuild reports recommended default → pass; unset + xcodebuild reports a different default → fail with the resolved value as evidence; xcodebuild silent → fall back to `(unset)`). Falls back to `{}` on timeout / missing-binary / network failure (private-package mirrors blocked) — every build-setting rule short-circuits with a recorded note in that case.
4. **Walk pbxproj plists** under `--project-path` to recover every `PBXShellScriptBuildPhase` and the `XCRemoteSwiftPackageReference` entries. The pbxproj is converted to XML via `plutil -convert xml1 -o -` and parsed with stdlib `plistlib`.
5. **Walk Package.resolved files** under `--project-path` (workspace + per-package nested copies); enumerate local SPM modules by their `Package.swift` manifests with a `*.swift` source count per module.
6. **Run analyzers** (`scripts/analyzers/*.py`) — script-phase, build-setting, asset-catalog, spm-graph. Each emits `Finding` and (build-setting only) `Recommendation` records.
7. **Rank** findings by impact category (`high` < `medium` < `low` < `unknown`) then descending estimated seconds.
8. **Persist** `diagnosis.json` to `--output-dir/diagnosis.json` with the schema-validated artifact.

## Outputs

A single JSON artifact conforming to [`schemas/diagnosis.schema.json`](../../schemas/diagnosis.schema.json).

Top-level fields:

- `schema_version` — `"1.0.0"`.
- `tool` — `{name: "ios-build-diagnose", version}`.
- `generated_at` — ISO-8601 UTC.
- `project` — path, build_system (`xcode` in v1), git_sha, git_branch, platform.
- `configuration` — scheme, configuration, destination.
- `inputs.measurement_artifact_path` — when supplied.
- `findings[]` — ranked F1–F9 findings (and equivalents on other projects). Each carries `rule_id`, `family` (`script-phase` / `build-setting` / `asset-catalog` / `spm`), `title`, `evidence`, `impact_category`, `wall_clock_predicted_seconds`, `citation`, `source_method`, `notes`.
- `additional_recommendations[]` — PR-#2 sandboxing + fuse recommendations. Same shape as `findings[]`. Counted separately so F1–F9 recall stays unambiguous.
- `summary` — `total_findings`, `total_additional_recommendations`, `by_impact`, `by_family`.
- `notes` — top-level run-time notes (xcodebuild fallback applied, adapter caveats, etc.).

## Failure modes (what this skill refuses to do)

- **No project mutation.** This skill never edits source files, project.pbxproj, build settings, or Package.resolved. The fix step lives in `ios-build-fix`.
- **No silent fabrication when xcodebuild is unavailable.** When `xcodebuild -showBuildSettings -json` is missing / times out / returns non-JSON, the build-setting rules short-circuit and a top-level note records the limitation. F1, F2, F3, F5, F6, F7, F8 still run on pbxproj + SPM data alone.
- **No build invocation.** This skill never runs `xcodebuild build`. Only `xcodebuild -showBuildSettings` (read-only) and stdlib filesystem reads.
- **No platform fudge.** `--platform` other than `ios` raises `ValueError`; v2 work is additive, not retrofitted.
- **No claims of measured impact for un-measured rules.** Each finding's `wall_clock_predicted_seconds.method` records whether the prediction is `measured-on-private-corpus` / `measured-on-wikipedia` / `heuristic` / `literature` so simulate and fix know which numbers are tunable.

## References

- Apple [Build Settings Reference](https://developer.apple.com/documentation/xcode/build-settings-reference) — F4 / F9 + PR-#2 audit citations.
- Apple WWDC22 [Demystify parallelization in Xcode builds (110364)](https://developer.apple.com/videos/play/wwdc2022/110364/) — F1, F2, F3, F8 + PR-#2 sandboxing/fuse citations. Verbatim quotes in `references/build-settings-best-practices.md` are verified against the session transcript.
- Apple [`xcodebuild` man page mirror](https://keith.github.io/xcode-man-pages/xcodebuild.1.html) — `-showBuildSettings -json` flags consumed by the adapter.
- Apple [Asset Management](https://developer.apple.com/documentation/xcode/asset-management) — F5 actool reference.
- Apple [Swift Packages](https://developer.apple.com/documentation/xcode/swift-packages) — F7 modularisation guidance, R1 dependency-rule semantics.
- [Tuist manifests guide](https://tuist.dev/en/docs/guides/features/projects/manifests) + [Bazel and Apple](https://bazel.build/docs/bazel-and-apple) — adapter detection logic shared with `ios-build-measure`.
- This skill bundles its own copy of `scripts/`, `schemas/`, and `references/`. Verify drift with `python3 scripts/verify-sync.py` from the repo root.

## Citation index + thresholds

- [`references/sources.md`](../../references/sources.md) — every URL with verification date.
- [`references/build-settings-best-practices.md`](../../references/build-settings-best-practices.md) — Why / Recommended / Measurement / Risk for COMPILATION_CACHE_ENABLE_CACHING, EAGER_LINKING, ENABLE_USER_SCRIPT_SANDBOXING, FUSE_BUILD_SCRIPT_PHASES.
- [`references/defaults.md`](../../references/defaults.md) — every analyzer threshold with the project + run that motivated it.
- [`references/critical-path-method.md`](../../references/critical-path-method.md) — v1 task-class-aggregate is the formal contract; per-target DAG attribution is its own dedicated workstream.
