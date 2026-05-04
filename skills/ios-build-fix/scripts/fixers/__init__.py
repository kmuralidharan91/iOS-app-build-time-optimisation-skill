"""Fixer registry + dataclasses for ios-build-fix (Phase A).

Each fixer module under this package exposes ``apply(findings, ctx)`` (and
companion ``preview`` / ``revert`` helpers) for a single rule_id. The
orchestrator in ``scripts/fix.py`` resolves a fixer by ``rule_id``,
gathers every diagnose finding sharing that rule_id, runs the
approval-gated preview, applies atomically on a throwaway branch, runs
the post-fix benchmark, computes the actual delta, and decides
``outcome ∈ {success, refused-null, refused-regressive, refused-noise,
refused-apply-error, refused-benchmark-error}``.

Per-rule aggregation (Phase A contract, mirrors the simulate contract):

- A fixer consumes ALL findings sharing its rule_id and applies ONE
  atomic edit that closes them collectively (e.g. F3's sandbox+fuse
  xcconfig change closes every ``script-phase/missing-output-declarations``
  finding at once).
- The fixer NEVER decides outcome — it only reports what it changed and
  what state the tree is in. The orchestrator owns the variance check
  and the refusal verdict so the policy lives in one place.
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any, Literal


FixKind = Literal[
    "delete-line",
    "edit-xcconfig",
    "edit-pbxproj",
    "compound",
    "no-op",
]
Family = Literal["script-phase", "build-setting", "asset-catalog", "spm"]


@dataclasses.dataclass(frozen=True)
class SubmoduleChange:
    """Records a submodule's pre- and post-fix HEAD when the fix touched it."""

    path: str
    sha_before: str
    sha_after: str


@dataclasses.dataclass(frozen=True)
class AppliedFix:
    """Result of applying a fix.

    ``files_modified`` is repository-relative (the parent worktree's
    repo, not the submodule's). ``submodule_changes`` is non-empty when
    the fix landed inside a submodule (e.g. F1's edit to
    ``REDACTED/scripts/XcodeBuildSteps/Step7_RunCrashlytics.sh``).
    The orchestrator uses these fields to write the ``applied_fix``
    block in fix-result.json and to revert cleanly on error.
    """

    kind: FixKind
    files_modified: tuple[str, ...]
    git_sha_before: str
    git_sha_after: str
    submodule_changes: tuple[SubmoduleChange, ...] = ()
    notes: str = ""


@dataclasses.dataclass
class FixContext:
    """Inputs every fixer can read.

    The orchestrator constructs this once per fix run.
    """

    diagnosis: dict[str, Any]
    simulation: dict[str, Any] | None
    project_root: pathlib.Path
    branch: str
    auto_approve: bool


def to_applied_fix_dict(applied: AppliedFix) -> dict[str, Any]:
    """Serialise an AppliedFix for the fix-result artifact."""

    return {
        "kind": applied.kind,
        "files_modified": list(applied.files_modified),
        "git_sha_before": applied.git_sha_before,
        "git_sha_after": applied.git_sha_after,
        "submodule_changes": [
            {
                "path": change.path,
                "sha_before": change.sha_before,
                "sha_after": change.sha_after,
            }
            for change in applied.submodule_changes
        ],
    }


class ApplyError(Exception):
    """Raised by a fixer when the apply step cannot be completed.

    The orchestrator catches this, runs ``git reset --hard HEAD``, deletes
    the throwaway branch, and emits ``outcome=refused-apply-error`` with
    the exception's message in ``outcome_reason``.
    """
