"""Fixers for the ``build-setting/*`` rule family.

Auto-applicable in v1: F4 (compilation-cache-disabled), F9
(eager-linking-disabled — designed null-delta refusal-path test). PR-#2's
``script-sandboxing-disabled`` and ``fuse-build-script-phases-disabled``
are co-located with F3 (script_phase.apply_missing_output_declarations
flips them as part of one xcconfig edit) so this module exposes thin
wrappers that delegate when those rule_ids are dispatched directly.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
from typing import Any

from . import AppliedFix, ApplyError, FixContext, SubmoduleChange


# F4 + F9 share the same Debug-xcconfig target; reuse the F3 helper.
def _debug_xcconfig(project_root: pathlib.Path) -> pathlib.Path:
    # TODO(public-cite: NetNewsWire) confirm the canonical xcconfig
    # layout for the public-cite project and add additional candidates
    # here. v1 supports a Configurations/Project/Local/local-debug.xcconfig
    # layout; the fix refuses cleanly when no candidate matches.
    candidates = [
        project_root / "Configurations" / "Project" / "Local" / "local-debug.xcconfig",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ApplyError(
        "build-setting fixer: could not locate local-debug.xcconfig under "
        "project root. v1 supports the Configurations/Project/Local "
        "xcconfig layout only."
    )


def preview_compilation_cache_disabled(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: FixContext,
) -> str:
    xcconfig = _debug_xcconfig(ctx.project_root)
    return (
        f"F4 compilation-cache-disabled — set in {xcconfig.relative_to(ctx.project_root)}:\n"
        "  + COMPILATION_CACHE_ENABLE_CACHING = YES\n"
        "(Predicted Δ -183.5s clean / +10s incremental — warm-cache test required.)"
    )


def apply_compilation_cache_disabled(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: FixContext,
) -> AppliedFix:
    return _append_xcconfig_setting(
        ctx,
        key="COMPILATION_CACHE_ENABLE_CACHING",
        value="YES",
        rule_id="build-setting/compilation-cache-disabled",
        commit_msg=(
            "F4: enable COMPILATION_CACHE_ENABLE_CACHING\n\n"
            "Closes diagnose finding rule_id=build-setting/compilation-cache-disabled.\n"
            "Predicted Δ -183.5s clean / +10s incremental (warm-cache)."
        ),
    )


def preview_eager_linking_disabled(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: FixContext,
) -> str:
    xcconfig = _debug_xcconfig(ctx.project_root)
    return (
        f"F9 eager-linking-disabled — set in {xcconfig.relative_to(ctx.project_root)}:\n"
        "  + EAGER_LINKING = YES\n"
        "(Predicted Δ 0.0s on the private corpus; designed null-delta refusal-path test.)"
    )


def apply_eager_linking_disabled(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: FixContext,
) -> AppliedFix:
    return _append_xcconfig_setting(
        ctx,
        key="EAGER_LINKING",
        value="YES",
        rule_id="build-setting/eager-linking-disabled",
        commit_msg=(
            "F9: enable EAGER_LINKING (designed null-delta refusal-path test)\n\n"
            "Closes diagnose finding rule_id=build-setting/eager-linking-disabled.\n"
            "Predicted Δ 0.0s; fix.py must report refusal honestly."
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _append_xcconfig_setting(
    ctx: FixContext,
    *,
    key: str,
    value: str,
    rule_id: str,
    commit_msg: str,
) -> AppliedFix:
    xcconfig = _debug_xcconfig(ctx.project_root)
    sha_before = _git_head(ctx.project_root)
    text = xcconfig.read_text()
    if _xcconfig_has_setting(text, key):
        return AppliedFix(
            kind="no-op",
            files_modified=(),
            git_sha_before=sha_before,
            git_sha_after=sha_before,
            submodule_changes=(),
            notes=f"{key} already present in xcconfig; no-op.",
        )
    block = (
        f"\n// MARK: - ios-build-fix ({rule_id})\n"
        f"{key} = {value}\n"
    )
    xcconfig.write_text(text.rstrip() + "\n" + block)

    rel = _relpath(ctx.project_root, xcconfig)
    _stage_and_commit(
        ctx.project_root, [rel], commit_msg, no_verify=ctx.no_verify_commits
    )
    submodule_changes = _detect_submodule_changes(ctx.project_root, [rel])
    sha_after = _git_head(ctx.project_root)
    return AppliedFix(
        kind="edit-xcconfig",
        files_modified=(rel,),
        git_sha_before=sha_before,
        git_sha_after=sha_after,
        submodule_changes=tuple(submodule_changes),
        notes=f"Added {key} = {value} to {rel}.",
    )


def _xcconfig_has_setting(text: str, key: str) -> bool:
    pattern = re.compile(rf"(?m)^\s*{re.escape(key)}\s*=")
    return bool(pattern.search(text))


def _git_head(path: pathlib.Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def _relpath(project_root: pathlib.Path, file_path: pathlib.Path) -> str:
    try:
        return str(file_path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return str(file_path)


def _stage_and_commit(
    project_root: pathlib.Path,
    rel_paths: list[str],
    message: str,
    no_verify: bool = False,
) -> None:
    # Same logic as script_phase._stage_and_commit. Re-imported here to
    # keep the modules independent (mirrors the simulators/ shape).
    from .script_phase import _stage_and_commit as _impl

    _impl(project_root, rel_paths, message, no_verify=no_verify)


def _detect_submodule_changes(
    project_root: pathlib.Path,
    files_modified: list[str],
) -> list[SubmoduleChange]:
    from .script_phase import _detect_submodule_changes as _impl

    return _impl(project_root, files_modified)
