# CHECKS.md — developer-facing summary of every diagnose check

> Authoritative TOC of every rule the v1 diagnose pass surfaces. Use this
> file as the at-a-glance index; the per-rule reasoning lives in
> [`references/build-settings-best-practices.md`](references/build-settings-best-practices.md),
> [`references/defaults.md`](references/defaults.md), and the analyzer
> source under [`scripts/analyzers/`](scripts/analyzers/).

## Table of contents

- [`findings[]` rules](#findings-rules-counted-toward-the-f1f9-effectiveness-gate) — F1–F9 effectiveness-gate rules
- [`additional_recommendations[]` rules](#additional_recommendations-rules-pr-2-audit-counted-separately-so-the-f1f9-recall-denominator-stays-unambiguous) — PR-#2 sandboxing + fuse audit
- [Suppression rules](#suppression-rules-intentionally-do-not-surface) — intentionally do not surface
- [Coverage boundaries](#coverage-boundaries) — adapter / platform / critical-path scope
- [Sources of truth](#sources-of-truth) — where the canonical rule data lives

## `findings[]` rules (counted toward the F1–F9 effectiveness gate)

| Rule id | Family | Inspects | Fires when | Impact | Citation |
| --- | --- | --- | --- | :---: | --- |
| `script-phase/random-sleep` | script-phase | Phase body + every invoked `.sh` file (followed via `bash $SRCROOT/...sh` resolution) | `sleep $RANDOM` regex match (`sleep $[(...$RANDOM...)]`, `sleep $((RANDOM%N))`, etc.) | high | [WWDC22 110364](https://developer.apple.com/videos/play/wwdc2022/110364/) |
| `script-phase/missing-debug-guard` | script-phase | Artifact-upload phases (name + body + invoked-script body) for a `CONFIGURATION` reference | Keyword match (`firebase`, `crashlytics`, `upload`, `dsym`, `fullstory`, `datadog`, `sentry`, `bugsnag`) AND no `CONFIGURATION` reference in the extended body | medium | [WWDC22 110364](https://developer.apple.com/videos/play/wwdc2022/110364/) |
| `script-phase/missing-output-declarations` | script-phase | `PBXShellScriptBuildPhase.outputPaths` | `outputPaths == []` | high (`alwaysOutOfDate=False`) / medium (`alwaysOutOfDate=True`) | [WWDC22 110364](https://developer.apple.com/videos/play/wwdc2022/110364/), [Xcode 14 release notes](https://developer.apple.com/documentation/xcode-release-notes/xcode-14-release-notes) |
| `script-phase/swiftlint-on-build` | script-phase | Phase name + body + invoked-script body | Regex `\bswiftlint\b` match | low | [WWDC22 110364](https://developer.apple.com/videos/play/wwdc2022/110364/) |
| `build-setting/compilation-cache-disabled` | build-setting | `xcodebuild -showBuildSettings` resolved value of `COMPILATION_CACHE_ENABLE_CACHING` (PR-#1 effective-settings semantics) | resolved value ≠ `YES` | high | [Apple Build Settings Reference](https://developer.apple.com/documentation/xcode/build-settings-reference) |
| `build-setting/eager-linking-disabled` | build-setting | `xcodebuild -showBuildSettings` resolved value of `EAGER_LINKING` | resolved value ≠ `YES` | low | [Apple Build Settings Reference](https://developer.apple.com/documentation/xcode/build-settings-reference) |
| `asset-catalog/incremental-recompile` | asset-catalog | Benchmark `measurement.json` `critical_path.incremental.nodes` | `CompileAssetCatalogVariant` `duration_seconds` ≥ 3.0 (tolerates both `dominant_task`/`duration_seconds` and `class_name`/`total_seconds` field shapes) | medium / high | [Apple Asset Management](https://developer.apple.com/documentation/xcode/asset-management) |
| `spm/swift-syntax-not-prebuilt` | spm | Every reachable `Package.resolved` | A pin's `identity == "swift-syntax"` exists | medium | [Xcode 26 release notes](https://developer.apple.com/documentation/xcode-release-notes/xcode-26-release-notes) — prebuilt-swift-syntax claim **UNVERIFIED at line level until the deferred verify confirms** |
| `spm/oversized-module` | spm | Every local `Package.swift` with a `*.swift` source-file count per module | `source_count ≥ 200` (high tier ≥ 600; medium tier ≥ 200) | high / medium | [Apple Swift Packages](https://developer.apple.com/documentation/xcode/swift-packages) |

## `additional_recommendations[]` rules (PR-#2 audit; counted separately so the F1–F9 recall denominator stays unambiguous)

| Rule id | Family | Inspects | Fires when | Impact | Citation |
| --- | --- | --- | --- | :---: | --- |
| `build-setting/script-sandboxing-disabled` | build-setting | `xcodebuild -showBuildSettings` resolved value of `ENABLE_USER_SCRIPT_SANDBOXING` | resolved value ≠ `YES` | medium (indirect — preconditions FUSE_BUILD_SCRIPT_PHASES) | [WWDC22 110364](https://developer.apple.com/videos/play/wwdc2022/110364/) |
| `build-setting/fuse-build-script-phases-disabled` | build-setting | `xcodebuild -showBuildSettings` resolved value of `FUSE_BUILD_SCRIPT_PHASES` | resolved value ≠ `YES` | medium (project-shape sensitive) | [WWDC22 110364](https://developer.apple.com/videos/play/wwdc2022/110364/) |

## Suppression rules (intentionally do NOT surface)

| Rule id | What | Suppressed when |
| --- | --- | --- |
| `spm/branch-pinned` | Package pinned by branch instead of version | Pin's `version != null` (the rule does **not** fire when every pin already resolves to a tagged version; surfacing it in that case would count as a false positive against the diagnose effectiveness gate) |

## Coverage boundaries

- **Adapter coverage**: v1 ships diagnose only on the Xcode adapter. The Tuist + Bazel adapter `script_phases`, `package_graph`, `show_build_settings` calls raise `NotImplementedError`. Diagnose for those build systems lands in v1.x.
- **Platform coverage**: v1 fences `platform="ios"`. Other platforms are accepted by the questionnaire but rejected with a "v2 not yet" error.
- **Critical-path inference**: see [`references/critical-path-method.md`](references/critical-path-method.md) — v1 uses `task-class-aggregate`, NOT a per-target DAG walk. Diagnose's asset-catalog rule reads the benchmark emit shape; per-target DAG attribution is its own deferred workstream.

## Sources of truth

- [`references/build-settings-best-practices.md`](references/build-settings-best-practices.md) — Why / Recommended / Measurement / Risk for every build-setting rule (incl. WWDC22 110364 verbatim quotes, verified verbatim against the session transcript).
- [`references/sources.md`](references/sources.md) — every citation URL with verification date.
- [`references/defaults.md`](references/defaults.md) — every threshold + heuristic tied to a `TODO(public-cite: <project>)` marker that names the project the threshold's evidence will be backfilled against.
- [`scripts/analyzers/`](scripts/analyzers/) — the actual rule implementations (`script_phase.py`, `build_setting.py`, `asset_catalog.py`, `spm_graph.py`, plus the `Finding`/`Recommendation` dataclasses in `__init__.py`).
- [`schemas/diagnosis.schema.json`](schemas/diagnosis.schema.json) — Draft 2020-12 schema (v1.0.0) every diagnose artifact validates against.

## Simulation predictors

Per-rule predictors live under [`scripts/simulators/`](scripts/simulators/); each one carries a tuning data point (the project + run that motivated the predicted Δ wall-clock). The static `wall_clock_predicted_seconds` block on each diagnose finding is the canonical predicted-Δ source until the simulate step refines it.
