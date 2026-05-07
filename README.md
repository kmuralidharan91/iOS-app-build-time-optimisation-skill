# iOS Build-Time Optimisation Skills

A suite of [Claude Code Agent Skills](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) that benchmark, diagnose, simulate, and fix iOS build-time problems across **Xcode**, **Tuist**, and **Bazel** projects.

> **Status — v0 (in development).** v1.0.0 ships once the doctor full-loop gate passes on the smoke-test corpus.

## The 5 skills

Decomposed by **user intent**, not by Xcode build-system technology layer.

| Skill | Answers the question | When to use |
| --- | --- | --- |
| [`ios-build-doctor`](skills/ios-build-doctor/) | "Just look at my build and tell me what to do." | Entry-point. Runs the questionnaire, detects the build system, dispatches the right specialist, ranks findings by wall-clock impact, asks for approval, hands off to the fixer, re-measures. |
| [`ios-build-measure`](skills/ios-build-measure/) | "How long does my build actually take, and what's getting better/worse over time?" | Benchmark + critical-path attribution + cross-run regression history. |
| [`ios-build-diagnose`](skills/ios-build-diagnose/) | "Why is it slow?" | Unified analyzer: project settings + script phases + Swift compile hotspots + SPM/BUILD graph — single tool, build-system-aware. |
| [`ios-build-simulate`](skills/ios-build-simulate/) | "What happens if I do X before I do it?" | Heuristic predictor for fix impact. Recommend-first, no project mutation. |
| [`ios-build-fix`](skills/ios-build-fix/) | "OK, apply this approved change and verify it actually helped." | Patcher that touches only what was approved, then re-measures and refuses if delta is null/regressive. |

## Differentiators

1. **Multi-build-system** — Xcode (primary), Tuist, Bazel via internal adapter pattern. Same diagnostics, three backends.
2. **Wall-clock attribution** — `critical_path.py` walks the build-timing DAG so findings are ranked by what *actually* prolongs the build, not cumulative compile aggregates.
3. **Real-project-tested defaults** — every threshold and heuristic is backed by runs against public iOS projects (Wikipedia iOS / Telegram-iOS / NetNewsWire). See `references/defaults.md`.
4. **Cross-run regression history** — `.build-history/` JSON-flat per-project DB keyed by git SHA; flags regressions over a sliding window.
5. **What-if impact simulation** — predicted Δ wall-clock per finding, labelled as prediction not measurement, with predicted-vs-actual reporting after each applied fix.

## Quickstart

> Quickstart commands will be filled in once the skills ship in chats 1–5. Until then this section is a placeholder.

```bash
# Install (placeholder):
git clone https://github.com/kmuralidharan91/iOS-app-build-time-optimisation-skill ~/repos/ios-build-skills
cp -R ~/repos/ios-build-skills/skills/* ~/.claude/skills/

# Run the doctor:
cd /path/to/your/ios-project
# In Claude Code: ask "use ios-build-doctor on this project"
```

## Platform scope

**v1 (this cycle): iOS only.** Adapters carry a `platform` parameter from day one so v2 (macOS/watchOS/tvOS/visionOS) is additive, not a rewrite. See `docs/PLAN.md` for the v2 roadmap.

## Repository layout

```
.
├── LICENSE                       # MIT, (c) 2026 Muralidharan Kathiresan
├── README.md                     # This file
├── AGENTS.md                     # Engineering principles + sync strategy + verify-sync.py contract
├── CHECKS.md                     # Developer-facing summary of every diagnose check
├── scripts/                      # Canonical scripts (synced into each skill — see AGENTS.md)
│   ├── adapters/                 # xcode / tuist / bazel
│   ├── benchmark.py              # ios-build-measure
│   ├── critical_path.py
│   ├── diagnose.py               # ios-build-diagnose
│   ├── simulate.py               # ios-build-simulate
│   ├── fix.py                    # ios-build-fix
│   ├── history_db.py
│   └── verify-sync.py            # CI gate: skill copies match canonical
├── schemas/                      # JSON schemas for measurement / diagnosis / simulation / history
├── references/                   # Synced into each skill; durable facts (settings, defaults, citations)
├── skills/
│   ├── ios-build-doctor/{SKILL.md, scripts/, references/, schemas/}
│   ├── ios-build-measure/{...}
│   ├── ios-build-diagnose/{...}
│   ├── ios-build-simulate/{...}
│   └── ios-build-fix/{...}
└── docs/
    ├── PLAN.md                   # Canonical execution plan
    ├── PROGRESS.md               # Append-only TodoWrite mirror
    ├── baseline/                 # Hand-authored ground truth per smoke target
    ├── smoke/<chat-N>/           # Per-chat smoke-test outputs
    └── verification/<chat-N>.md  # Per-chat clean-room verification log
```

Each `skills/<name>/` directory is self-contained, matching the [canonical anthropics/skills layout](https://github.com/anthropics/skills/tree/main/skills/pdf). Sync from canonical roots is enforced by `scripts/verify-sync.py`.

## License

MIT — see [`LICENSE`](LICENSE).
