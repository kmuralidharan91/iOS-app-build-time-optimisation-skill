# Build-setting recommendations — `references/build-settings-best-practices.md`

> Companion file to `scripts/analyzers/build_setting.py`. Every audit
> entry below carries a **Why**, **Recommended**, **Measurement**, and
> **Risk** subsection. WWDC quotes are verified verbatim against the
> session transcript and tracked in `references/sources.md`.

## `COMPILATION_CACHE_ENABLE_CACHING`

Rule id: `build-setting/compilation-cache-disabled` (F4 ground truth).

**Why.** Xcode 16+ ships a project-independent on-disk compilation
cache. Setting `COMPILATION_CACHE_ENABLE_CACHING=YES` lets the
build system reuse compile artifacts across `clean`, branch
checkouts, and simulator OS bumps — work that would otherwise
recompute even after a one-line code change provided the changed
file's downstream cone overlaps cached artifacts.

**Recommended.** `YES` for Debug (and any local-development
configuration). Leave Release / Distribution decisions to the team
that ships builds — release pipelines often want `clean` semantics
to avoid masking determinism bugs.

**Measurement.** On the development-time internal corpus a warm-cache
clean Debug+sim build came in **~45.6% faster** than the cold-cache
equivalent (≈125 s saved on a 275 s baseline). Numbers vary with
project shape. v1.0.0 evidence: both Wikipedia-iOS@`9200297c15`
(`docs/wikipedia-ios-analysis.md:87`) and NetNewsWire@`build-comparison-base`
(`docs/netnewswire-analysis.md:89`) ship with
`COMPILATION_CACHE_ENABLE_CACHING` **unset** (universal miss confirms
the rule fires). Measured warm-cache Δ on NetNewsWire ships in
`build-benchmarks/netnewswire/fix-F4/fix-result.json` (Phase B step 14).

**Risk.** Incremental builds can pay a small cache-invalidation cost
(~10 s extra on touched-file change observed during development)
because the cache sees a wider invalidation cone than Xcode's
per-target incremental tracker. Track the per-project trade-off rather
than enabling blindly; the fixer's re-measure step refuses to claim
success when net Δ is null/regressive (see
`build-benchmarks/netnewswire/fix-F4/fix-result.json` for the v1.0.0
data point).

## `EAGER_LINKING`

Rule id: `build-setting/eager-linking-disabled` (F9 ground truth).

**Why.** WWDC22 110364 introduced eager linking: a downstream
target can begin its `Ld` task as soon as the upstream target's
`emit-module` task completes, instead of waiting for the upstream
`Ld` to finish first. Shortens the critical path on projects whose
linker waits dominate Debug builds.

**Recommended.** `YES` is the documented default for projects that
fit the eager-link shape (pure-Swift dynamic frameworks linked by
their dependents). Diagnose flags `unset` / `NO` and lets simulate +
fix decide whether the project's actual graph benefits.

**Measurement.** On the development-time internal corpus, enabling
`EAGER_LINKING` measured **zero clean-build improvement** and the change
was reverted. F9's `impact_category=low` reflects that. v1.0.0 evidence:
both Wikipedia-iOS@`9200297c15` (`docs/wikipedia-ios-analysis.md:86`)
and NetNewsWire@`build-comparison-base`
(`docs/netnewswire-analysis.md:88`) ship with `EAGER_LINKING` unset
(universal miss). The designed null-delta refusal-path test on
NetNewsWire ships in `build-benchmarks/netnewswire/fix-F9/fix-result.json`
(expected `outcome=refused-null`; Phase B step 14).

**Risk.** Almost none — the optimisation only changes scheduling.
The mitigation when impact is null is to revert; the
`ios-build-fix` re-measure step refuses to claim success when delta
is null or regressive, which catches this case automatically.

## `ENABLE_USER_SCRIPT_SANDBOXING`

Rule id: `build-setting/script-sandboxing-disabled` (PR-#2;
`additional_recommendations[]`).

**Why.** WWDC22 110364, verbatim:

> Sandboxing is an opt-in feature that blocks shell scripts from accidentally accessing source files and intermediate build objects, unless those are explicitly declared as an input or output for the phase.

Sandboxing is the precondition for `FUSE_BUILD_SCRIPT_PHASES` parallelisation.

**Recommended.** `YES`. The same WWDC22 session, verbatim:

> To enable Sandboxed Shell Scripts for a target, set ENABLE_USER_SCRIPT_SANDBOXING to YES in the build settings editor or an xcconfig file.

Apply per target (and as a project-default once existing phases have correct input/output declarations).

**Measurement.** Indirect — sandboxing itself does not cut wall-clock. The wall-clock win comes from the `inputPaths` / `outputPaths` audit it forces (every undeclared input becomes a build error), which is what lets the build system correctly skip phases when inputs are unchanged. WWDC22 110364, verbatim:

> sandboxed shell scripts allow having correct dependency information to enable faster and more robust incremental builds since the build system has the confidence to skip script phases if the inputs haven't changed and the outputs are still valid

**Risk.** Existing phases with undeclared dependencies will fail to
build until they're fixed. Apply incrementally, target-by-target;
`ios-build-fix` carries the per-finding refusal-when-broken
guarantee.

## `FUSE_BUILD_SCRIPT_PHASES`

Rule id: `build-setting/fuse-build-script-phases-disabled` (PR-#2;
`additional_recommendations[]`).

**Why.** WWDC22 110364 introduced parallel script-phase execution. Verbatim:

> If the scripts in a target are configured to run based on dependency analysis and specify their complete list of inputs and outputs, then the build setting FUSE_BUILD_SCRIPT_PHASES can be set to YES to indicate the build system should attempt to run them in parallel.

Without it, all script phases on a target serialise.

**Recommended.** `YES`, **after** sandboxing is on and every phase
declares its inputs / outputs. The rule's `notes[]` reminds the user
of that prerequisite ordering.

**Measurement.** Wall-clock win scales with phase count and the
spawn / setup overhead per phase. The fuse win amortises
shell-startup time across the phase chain. Project-shape sensitive.
v1.0.0 reference counts: Wikipedia-iOS@`9200297c15` = 6 phases,
NetNewsWire@`build-comparison-base` = 8 phases. Per-phase magnitude
once the F3 prerequisite (correct input/output declarations) is applied
ships in `build-benchmarks/netnewswire/fix-F3/fix-result.json` (Phase B
step 14).

**Risk.** WWDC22 110364, verbatim:

> However, when running script phases in parallel, the build system has to rely on the specified inputs and outputs. So be aware that an incomplete list of the inputs or outputs of a script phase can lead to data races which are very hard to debug.

Mitigation: enable sandboxing first; sandbox failure mode surfaces undeclared dependencies as build errors instead of silent data races.

## `IDEPackageEnablePrebuilts` (Xcode 26 user-defaults; macro-using projects)

Rule id: `spm/swift-syntax-not-prebuilt` (F6 ground truth).

**Why.** Xcode 26 ships a prebuilt `swift-syntax` library that is downloaded from swift.org and integrated into the build graph for macro-using targets, replacing the per-clean-build source compile of swift-syntax that has historically dominated clean-build cost on projects pulling macros transitively. Apple's Xcode 26 release notes, "Swift Macros Build Performance → New Features", verbatim:

> Build for Swift macro targets is accelerated by downloading a prebuilt library for swift-syntax from swift.org and integrating it into the build. This feature is enabled automatically and will improve build times for these projects.  (151701829)

(Verified line-level via Apple's SPA JSON endpoint `https://developer.apple.com/tutorials/data/documentation/xcode-release-notes/xcode-26-release-notes.json`; the HTML page is marketing-shell-only.)

**Recommended.** Use Xcode 26 (or later); leave `IDEPackageEnablePrebuilts` at its automatic-on default. There is **no project-side build setting** to flip — the mechanism is opt-in by virtue of Xcode version, not by xcconfig.

**Measurement.** Estimated 5–20 s clean-build savings depending on how
many macro-using packages reach `swift-syntax` transitively. Neither
v1.0.0 corpus (Wikipedia-iOS, NetNewsWire) pulls swift-syntax — see
`references/defaults.md` "spm/swift-syntax-not-prebuilt" section for
the Package.resolved evidence. Magnitude calibration is **deferred to
v1.1** against a project that actually pulls swift-syntax (e.g. a
SwiftFormat-using app). Simulate predicts -12 s ±7 s for F6 with
`confidence=low` (heuristic; project-shape sensitive).

**Risk.** Apple's Xcode 26 release notes list two known build-failure modes for macro-dependent projects + one Legacy Preview Execution issue, all worked around by disabling the feature via `defaults write com.apple.dt.Xcode IDEPackageEnablePrebuilts NO`. Apply that workaround only when build failures around `_SwiftSyntaxCShims` appear; it disables the speedup repo-wide for that user.

## How analyzers read this file

`scripts/analyzers/build_setting.py` does **not** parse this file at
runtime — every rule body has the rule id, the WWDC / Apple URL,
and the impact category baked into the analyzer source. This file
is the human-facing reference that the SKILL.md links to and that
simulate / fix consume to construct user-facing explanations.

When adding a new build-setting rule, the workflow is:

1. Add a section to this file using the **Why / Recommended /
   Measurement / Risk** pattern.
2. Add the rule to `scripts/analyzers/build_setting.py` with a
   citation pointing at the same WWDC/Apple URL.
3. Add the citation row to `references/sources.md` with a
   verification date.
4. `grep -F` any verbatim quote against its on-disk source and log
   the result in the verification log for that change.
