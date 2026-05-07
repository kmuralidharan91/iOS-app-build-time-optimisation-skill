"""Fixers for ``spm/*`` rule family.

v1 surface: F6 (swift-syntax-not-prebuilt) and F7 (oversized-module) are
informational. F6's actual fix is "upgrade to Xcode 26"
(``IDEPackageEnablePrebuilts`` is on by default); fix.py reports the
status rather than mutating the project. F7's fix ("split the module")
is project-architectural and not auto-applicable. Both emit a recipe.
"""

from __future__ import annotations

import subprocess
from typing import Any

from . import AppliedFix, FixContext


def preview_swift_syntax_not_prebuilt(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: FixContext,
) -> str:
    return (
        "F6 spm/swift-syntax-not-prebuilt — informational in v1.\n"
        "Xcode 26 enables IDEPackageEnablePrebuilts automatically (verified\n"
        "via Xcode 26 release notes — see references/sources.md). The fix\n"
        "is to build with Xcode 26 or later; no project mutation required.\n"
        "If you need to opt out for legacy reasons:\n"
        "  defaults write com.apple.dt.Xcode IDEPackageEnablePrebuilts NO"
    )


def apply_swift_syntax_not_prebuilt(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: FixContext,
) -> AppliedFix:
    return _no_op(
        ctx,
        "F6 fixer is informational in v1; upgrade to Xcode 26 for the win. "
        "No project mutation.",
    )


def preview_oversized_module(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: FixContext,
) -> str:
    return (
        "F7 spm/oversized-module — informational in v1.\n"
        "Recommended manual fix: split the module along feature seams.\n"
        "Cannot be auto-applied; requires architectural review."
    )


def apply_oversized_module(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: FixContext,
) -> AppliedFix:
    return _no_op(
        ctx,
        "F7 fixer is informational in v1; module-split is architectural.",
    )


def _no_op(ctx: FixContext, note: str) -> AppliedFix:
    sha = subprocess.check_output(
        ["git", "-C", str(ctx.project_root), "rev-parse", "HEAD"], text=True
    ).strip()
    return AppliedFix(
        kind="no-op",
        files_modified=(),
        git_sha_before=sha,
        git_sha_after=sha,
        submodule_changes=(),
        notes=note,
    )
