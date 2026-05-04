# CHECKS.md — developer-facing summary of every diagnose check

> **Status — placeholder.** Populated in Phase A when `ios-build-diagnose` and `references/build-settings-best-practices.md` ship. Each row will list: rule id, check name, what it inspects, when it fires, wall-clock impact category, and which Apple/WWDC/Tuist/Bazel source it cites.

## Layout (final form, after Phase A)

| Rule id | Check | Inspects | Fires when | Impact | Citation |
| --- | --- | --- | --- | --- | --- |
| _xcb-001_ | _example placeholder_ | _setting / phase / file_ | _condition_ | _high / medium / low_ | _Apple docs URL or WWDC session_ |
| _… | _… | _… | _… | _… | _…_ |

## Categories of checks (planned in Phase A)

- **Build settings hygiene** — effective values resolved via `xcodebuild -showBuildSettings`, comparing pbxproj-explicit vs Xcode-default-resolved.
- **Script phases** — `ENABLE_USER_SCRIPT_SANDBOXING`, `FUSE_BUILD_SCRIPT_PHASES`, output-file declarations, parallelism barriers.
- **Swift compilation** — type-checker hotspots, `-debug-time-function-bodies` outliers, large generic instantiations, expression-checker timeouts.
- **Module graph** — circular dependencies, oversized targets, missing `DEFINES_MODULE`, suboptimal explicit-modules configuration.
- **Package graph** — SPM resolution overhead, duplicate module variants, circular package deps, build-plugin overhead.
- **Tuist-specific** — manifest cache health, oversized graphs, target dependencies that defeat Tuist's caching.
- **Bazel-specific** — non-cacheable rules, rule_apple version drift, rules_swift modes (explicit modules, integrated driver).

## Sources of truth

- `references/build-settings-best-practices.md` — the per-setting deep dive (Phase A deliverable).
- `references/sources.md` — full citation index (Phase A deliverable).
- `references/defaults.md` — every threshold and heuristic with its tuning project + run (Phase A deliverable).

## Until Phase A

Treat this file as a TOC stub. The actual checks live in code (`scripts/diagnose.py`) and in the references (`references/`). Once Phase A lands, this file becomes the at-a-glance index every developer reads first.
