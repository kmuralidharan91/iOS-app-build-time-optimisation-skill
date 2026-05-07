# AGENTS.md — engineering principles for the iOS Build-Time Optimisation Skill suite

> **Read this before editing any skill, script, or schema.** This file documents non-negotiable principles, the build-system adapter contract, the questionnaire-first UX, the sync strategy, and the v1-iOS-only platform fence.

## Non-negotiable principles

1. **Recommend-first, never-mutate-without-approval.** No skill modifies the user's project before showing the proposed change and getting explicit approval. `ios-build-fix` is the only skill that touches project files, and only after `ios-build-doctor` (or the user directly) has approved a specific finding. Approval is per-finding, not per-batch.
2. **Wall-clock is the primary metric.** Findings ranked by predicted Δ wall-clock impact, not by cumulative compile time, not by file size, not by alphabetical order. If a finding can't be tied to a wall-clock category (`high` ≥ 30s, `medium` 5–30s, `low` < 5s, `unknown`), it's not shipped. Wall-clock attribution comes from `scripts/critical_path.py`'s DAG walk, not from `xcodebuild -showBuildTimingSummary`'s cumulative aggregates.
3. **Questionnaire first.** `ios-build-doctor` opens with the 8-question structured questionnaire before any analysis runs. Other skills can run standalone, but the doctor is the canonical UX.
4. **Cite Apple/WWDC/Tuist/Bazel for every recommendation.** Every diagnose finding includes a citation to a primary source (`developer.apple.com`, WWDC session, `docs.tuist.dev`, `bazel.build`). Citations live in `references/sources.md`. No hand-wavy "this should be faster" reasoning.
5. **Real-project-tested defaults only.** Every threshold (variance, regression sensitivity, simulation rule magnitude) cites the project + run that motivated it. References in `references/defaults.md`.
6. **Honesty about predictions.** Output from `ios-build-simulate` is always labelled "predicted Δ", not "Δ". `ios-build-fix` reports a predicted-vs-actual delta after every fix and refuses to claim success on a null or regressive measurement (within the variance threshold).

## Platform scope (v1)

**v1 ships iOS only.** Adapters carry a `platform` parameter from day one; v2 adds macOS/watchOS/tvOS/visionOS without re-architecture. See [`docs/PLAN.md`](docs/PLAN.md) for the v2 roadmap.

The questionnaire asks "iOS, macOS, watchOS, tvOS, or visionOS?". In v1 only `"ios"` is accepted; other answers route to a "v2 not yet" message. The question itself stays so v2 can flip the gate.

## Build-system adapter contract

Three adapters live under `scripts/adapters/`:

| Adapter | Detected by | Primary commands |
| --- | --- | --- |
| `xcode_adapter.py` | `*.xcodeproj` and/or `*.xcworkspace` exist; no Tuist/Bazel manifests | `xcodebuild -showBuildSettings -json`, `xcodebuild build -showBuildTimingSummary` |
| `tuist_adapter.py` | `Project.swift` at root (Tuist's required manifest per [docs.tuist.dev](https://docs.tuist.dev/en/guides/features/projects/manifests)) | `tuist build`, `tuist graph`, parse `Project.swift` AST via Tuist's own tooling |
| `bazel_adapter.py` | `MODULE.bazel` (Bzlmod) or `WORKSPACE`/`WORKSPACE.bazel` (legacy) plus `BUILD.bazel`/`BUILD` files (per [bazel.build/docs/bazel-and-apple](https://bazel.build/docs/bazel-and-apple)) | `bazelisk build //...`, `bazel query`, parse `BUILD` files |

**Tie-breaker.** A project may have both Tuist + a generated `*.xcodeproj`, or Bazel + an Xcode wrapper. Prefer Tuist > Bazel > Xcode for source-of-truth selection. The questionnaire confirms with the user when ambiguity is detected.

**Required adapter API** (every adapter implements these signatures):

```python
def detect(project_path: pathlib.Path) -> bool: ...
def measure(project_path: pathlib.Path, scheme: str | None, configuration: str,
            destination: str, platform: Literal["ios"]) -> BenchmarkResult: ...
def show_build_settings(project_path: pathlib.Path, scheme: str | None,
                        configuration: str, platform: Literal["ios"]) -> dict[str, str]: ...
def script_phases(project_path: pathlib.Path,
                  platform: Literal["ios"]) -> list[ScriptPhase]: ...
def package_graph(project_path: pathlib.Path,
                  platform: Literal["ios"]) -> PackageGraph: ...
```

Fix application is **not** an adapter responsibility in v1.0.0. The fix-step design lives outside the adapter surface in per-rule fixer modules orchestrated by a single CLI: see [`scripts/fixers/registry.py`](scripts/fixers/registry.py) for the registered fixer modules (script-phase / build-setting / asset-catalog / spm-graph) and [`scripts/fix.py`](scripts/fix.py) for the orchestrator that builds a throwaway branch, applies the approved patch, re-measures, and refuses on null/regressive deltas. The Xcode adapter is the v1.0.0-must-ship target. Tuist + Bazel adapters ship measurement parts in v1.0.0; full diagnose for non-Xcode systems is deferred to v1.0.1+.

## Sync strategy — chosen: byte-identical copies + verify-sync.py

**Decision.** Each `skills/<name>/` directory contains its own copies of the canonical scripts, schemas, and references it needs. A `scripts/verify-sync.py` gate compares each skill copy to the canonical version under repo root and exits non-zero on drift. Run as a pre-commit hook locally and as a CI gate when the repo flips public.

**Why this over the alternatives:**

| Option | Pro | Con | Verdict |
| --- | --- | --- | --- |
| Symlinks within the repo | Zero-cost sync | Symlinks survive `gh repo clone` poorly on Windows; users copying a single skill dir into `~/.claude/skills/` get dangling links | Rejected |
| Single-source design (skill dirs hold only `SKILL.md`, paths point to repo-root canonical) | No duplication | Breaks the [Anthropic Skills loader convention](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) where each `skills/<name>/` is self-contained; users can't install one skill, only the entire repo | Rejected |
| **Byte-identical copies + verify-sync.py** | Each skill self-contained, matches canonical anthropics/skills layout (e.g. [`pdf`](https://github.com/anthropics/skills/tree/main/skills/pdf), [`skill-creator`](https://github.com/anthropics/skills/tree/main/skills/skill-creator)); users can copy one skill into `~/.claude/skills/` and it works standalone | Storage duplication (negligible — KB scale); needs a sync gate to prevent drift | **Chosen** |

`verify-sync.py` computes a SHA-256 of every canonical file under `scripts/`, `schemas/`, `references/`, then for each `skills/<name>/{scripts,schemas,references}/<file>` compares the SHA. Mismatches print the diff and exit 1.

## Per-skill `SKILL.md` style guide

- **Frontmatter** — `name` (lowercase + hyphens, ≤ 64 chars, must NOT contain `anthropic` or `claude`), `description` (≤ 1024 chars, third person, includes both *what* and *when*) per the [Anthropic Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview#skill-structure).
- **Body length** — ≤ 500 lines per the [authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices#token-budgets). Push deep references to one-level-deep `references/*.md` files.
- **Sections required**: a "When to use" paragraph, a numbered workflow ("step 1: …, step 2: …"), an "Outputs" section that lists the schema, and a "Failure modes" section that names what the skill refuses to do.
- **MCP tool references** — fully qualified: `XcodeBuildMCP:build_sim`, not `build_sim`.
- **No time-sensitive language** — no "after Q3 2026"; archive obsolete patterns under an "Old patterns" `<details>` block instead.

## Smoke-test corpus and effectiveness gates

Each user-visible script is smoke-tested against a private iOS corpus during development; the public release backfills the same gates against public corpora — Wikipedia-iOS for the Tuist build system, NetNewsWire for the pure-Xcode build system, Telegram-iOS for the Bazel build system. See [`docs/PLAN.md`](docs/PLAN.md) "Effectiveness gate" for the per-skill numeric gates.

## Clean-room posture

This is original work, not a derivative. Each multi-file change ends with a clean-room verification log that proves zero textual overlap with any earlier prior-art codebase used during exploration.

## Commit + push protocol

- One TodoWrite item `in_progress` at a time. Mark complete IMMEDIATELY on finish.
- Every TodoWrite state change appends a line to `docs/PROGRESS.md` (ISO-8601 UTC), commit, push.
- Commit messages follow the project style (HEREDOC, "what changed and why"). No emojis unless the user asks.
- Never push to remote main without an explicit user instruction. (This repo has no protected branches in v0; revisit when v1.0.0 ships.)
