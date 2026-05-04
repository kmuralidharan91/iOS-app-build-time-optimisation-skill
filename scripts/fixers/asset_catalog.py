"""Fixer for ``asset-catalog/incremental-recompile`` (Phase A).

This rule fires when ``CompileAssetCatalogVariant`` lands on the Phase A
incremental critical path with duration ≥ 3 s. The fix is project-shape
sensitive (which images invalidate, which catalog, which app-icon set);
v1 emits a manual-followup recipe and ``no-op``-applies. The orchestrator
gates auto-apply behind ``--allow-manual``; without it, fix.py refuses.
"""

from __future__ import annotations

import subprocess
from typing import Any

from . import AppliedFix, FixContext


def preview_incremental_recompile(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: FixContext,
) -> str:
    return (
        "F5 asset-catalog/incremental-recompile — informational fix in v1.\n"
        "Recommended manual steps:\n"
        "  1. Re-export the AppIcon set from a single canonical source.\n"
        "  2. Ensure no editor (Sketch / Figma / preview tooling) re-saves\n"
        "     PNG metadata on every open.\n"
        "  3. If catalog invalidates on every build, run\n"
        "     `xcrun actool --print-asset-pack-manifest` to inspect and prune\n"
        "     duplicate variants.\n"
        "Auto-apply is intentionally not implemented in v1."
    )


def apply_incremental_recompile(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: FixContext,
) -> AppliedFix:
    sha = subprocess.check_output(
        ["git", "-C", str(ctx.project_root), "rev-parse", "HEAD"], text=True
    ).strip()
    return AppliedFix(
        kind="no-op",
        files_modified=(),
        git_sha_before=sha,
        git_sha_after=sha,
        submodule_changes=(),
        notes=(
            "F5 fixer is informational in v1; no auto-apply. "
            "Run with --allow-manual to acknowledge."
        ),
    )
