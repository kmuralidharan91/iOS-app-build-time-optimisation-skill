"""Fixers for the ``script-phase/*`` rule family (Phase A).

Auto-applicable in v1: F1 (random-sleep), F3 (missing-output-declarations
via ENABLE_USER_SCRIPT_SANDBOXING + FUSE_BUILD_SCRIPT_PHASES). F2 and F8
emit a manual-followup recipe; the orchestrator gates auto-apply behind
``--allow-manual`` for those rules.

The F3 fixer applies the sandbox+fuse xcconfig change ONLY — it
deliberately does not edit pbxproj outputPaths. Rationale: the simulate
predictor's sqrt(N)×4 cap models post-sandbox+fuse parallel fan-out,
which sandbox+fuse alone unlock; explicit outputPaths is a polish pass.
This keeps the fix surgical and the predicted-vs-actual comparison
clean.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
from typing import Any

from . import AppliedFix, ApplyError, FixContext, SubmoduleChange


_RANDOM_SLEEP_PATTERN = re.compile(
    r"^\s*sleep\s+\$\[\s*\(\s*\$RANDOM\s*%\s*\d+\s*\)\s*\+\s*\d+\s*\]s\s*$"
)


def preview_random_sleep(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: FixContext,
) -> str:
    """Return a human-readable diff preview for the F1 fix."""

    lines = ["F1 random-sleep — delete one literal line per finding:"]
    for idx, finding in findings:
        evidence = finding.get("evidence") or {}
        if evidence.get("kind") != "file_line":
            continue
        path = evidence.get("path", "<missing path>")
        line = evidence.get("line", "?")
        lines.append(f"  - {path}:{line}  -- 'sleep $[ ($RANDOM % N) + M ]s' (or matching variant)")
    return "\n".join(lines)


def apply_random_sleep(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: FixContext,
) -> AppliedFix:
    """Delete every random-sleep line attributed to F1.

    Each F1 finding has ``evidence.path`` + ``evidence.line``. We open
    the file, locate the matching ``sleep $[ ... ]s`` line by regex (the
    line number is a hint; the regex is the authority because the file
    might have been edited since diagnose ran), and delete it. If the
    file is in a submodule, we record the submodule SHA before/after.

    Atomicity: every file is rewritten in-memory then atomically
    replaced. On any error during the loop, ``ApplyError`` is raised; the
    orchestrator handles the ``git reset --hard`` rollback.
    """

    sha_before = _git_head(ctx.project_root)
    files_modified: list[str] = []
    submodule_changes: list[SubmoduleChange] = []

    for _idx, finding in findings:
        evidence = finding.get("evidence") or {}
        if evidence.get("kind") != "file_line":
            raise ApplyError(
                f"F1 finding has unexpected evidence.kind={evidence.get('kind')!r}"
            )
        path_str = evidence.get("path")
        line_hint = evidence.get("line")
        if not path_str:
            raise ApplyError("F1 finding missing evidence.path")
        path = _resolve_finding_path(ctx.project_root, path_str)
        if path is None or not path.is_file():
            raise ApplyError(f"F1 fix target not found on disk: {path_str}")

        text = path.read_text()
        new_text, removed = _delete_random_sleep_line(text, hint=line_hint)
        if not removed:
            raise ApplyError(
                f"F1: no random-sleep line matched in {path} (hint line={line_hint})"
            )
        path.write_text(new_text)
        rel = _relpath(ctx.project_root, path)
        files_modified.append(rel)

    files_modified = sorted(set(files_modified))
    _stage_and_commit(
        ctx.project_root,
        files_modified,
        "F1: remove random sleep from script phases\n\n"
        f"Touches {len(files_modified)} file(s); rule_id=script-phase/random-sleep.",
        no_verify=ctx.no_verify_commits,
    )
    submodule_changes = _detect_submodule_changes(ctx.project_root, files_modified)

    sha_after = _git_head(ctx.project_root)
    return AppliedFix(
        kind="delete-line",
        files_modified=tuple(files_modified),
        git_sha_before=sha_before,
        git_sha_after=sha_after,
        submodule_changes=tuple(submodule_changes),
        notes=f"Removed {len(findings)} random-sleep line(s).",
    )


def preview_missing_output_declarations(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: FixContext,
) -> str:
    """Return a human-readable preview for the F3 fix.

    v1 only enables ``FUSE_BUILD_SCRIPT_PHASES``; the companion
    ``ENABLE_USER_SCRIPT_SANDBOXING`` requires every existing script
    phase to declare its inputs/outputs and breaks REDACTED's "Step 4 - Copy
    Localizable Resources..." (and similar) until those declarations
    land. Empirically validated 2026-05-04: enabling both together on
    REDACTED ``develop`` @ ``REDACTED`` produces ``** BUILD FAILED **`` with
    sandbox-denied PhaseScriptExecution. Predicted impact drops from
    -15.5s combined (sandbox+fuse) to roughly the PR-#2 fuse-only band
    of -7s clean / -5.6s incremental.
    """

    xcconfig = _f3_xcconfig_target(ctx.project_root)
    return (
        f"F3 missing-output-declarations — enable fuse via "
        f"{xcconfig.relative_to(ctx.project_root)}:\n"
        "  + FUSE_BUILD_SCRIPT_PHASES = YES\n"
        f"({len(findings)} source finding(s) closed by this single edit. "
        "ENABLE_USER_SCRIPT_SANDBOXING is intentionally NOT set in v1 — "
        "it requires explicit outputPaths on every existing script phase, "
        "which is project-specific. Sandbox support lands in v1.x once "
        "the pbxproj-edit fixer is implemented.)"
    )


def apply_missing_output_declarations(
    findings: list[tuple[int, dict[str, Any]]],
    ctx: FixContext,
) -> AppliedFix:
    """Append FUSE_BUILD_SCRIPT_PHASES = YES to the Debug xcconfig.

    v1 deliberately scopes the F3 fix to fuse-only. Sandbox + outputPaths
    is a v1.x enhancement (requires per-phase pbxproj edits). Empirically
    validated 2026-05-04 against REDACTED develop @ REDACTED: sandbox ON
    without outputPaths breaks "Step 4 - Copy Localizable Resources"
    with sandbox-denied PhaseScriptExecution → ** BUILD FAILED **.
    """

    xcconfig = _f3_xcconfig_target(ctx.project_root)
    sha_before = _git_head(ctx.project_root)
    text = xcconfig.read_text()
    if _xcconfig_has_setting(text, "FUSE_BUILD_SCRIPT_PHASES"):
        return AppliedFix(
            kind="no-op",
            files_modified=(),
            git_sha_before=sha_before,
            git_sha_after=sha_before,
            submodule_changes=(),
            notes="F3: FUSE_BUILD_SCRIPT_PHASES already present in xcconfig.",
        )

    block = (
        "\n// MARK: - ios-build-fix F3 (script-phase/missing-output-declarations)\n"
        "// Predicted Δ -7s clean / -5.6s incremental (fuse-only; v1 omits\n"
        "// sandbox until per-phase outputPaths fixer lands in v1.x).\n"
        "FUSE_BUILD_SCRIPT_PHASES = YES\n"
    )
    new_text = text.rstrip() + "\n" + block
    xcconfig.write_text(new_text)

    rel = _relpath(ctx.project_root, xcconfig)
    _stage_and_commit(
        ctx.project_root,
        [rel],
        "F3: enable FUSE_BUILD_SCRIPT_PHASES (fuse-only; sandbox deferred)\n\n"
        "Closes diagnose findings rule_id=script-phase/missing-output-declarations.\n"
        "Sandbox NOT enabled — would break REDACTED's Step 4 phase until outputPaths "
        "are declared per script phase (pbxproj edit, deferred to v1.x).",
        no_verify=ctx.no_verify_commits,
    )
    submodule_changes = _detect_submodule_changes(ctx.project_root, [rel])
    sha_after = _git_head(ctx.project_root)
    return AppliedFix(
        kind="edit-xcconfig",
        files_modified=(rel,),
        git_sha_before=sha_before,
        git_sha_after=sha_after,
        submodule_changes=tuple(submodule_changes),
        notes=f"Added FUSE_BUILD_SCRIPT_PHASES=YES to {rel}.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_finding_path(project_root: pathlib.Path, raw: str) -> pathlib.Path | None:
    """Resolve a diagnose-recorded path against the project root.

    Diagnose can record an absolute path (e.g.
    ``/private/tmp/REDACTED-develop-Phase A/...``) that points to a worktree
    that no longer exists. We try in order: as-is, basename under
    project_root recursively, then None.
    """

    candidate = pathlib.Path(raw)
    if candidate.is_file():
        return candidate.resolve()
    # Try absolute paths whose suffix sits under the project_root.
    parts = candidate.parts
    for i in range(len(parts)):
        rel = pathlib.Path(*parts[i:])
        sub = (project_root / rel).resolve()
        if sub.is_file():
            return sub
    # Fallback: glob by basename.
    matches = list(project_root.rglob(candidate.name))
    matches = [m for m in matches if m.is_file()]
    if len(matches) == 1:
        return matches[0]
    return None


def _delete_random_sleep_line(
    text: str,
    hint: int | None,
) -> tuple[str, bool]:
    """Strip every line matching the random-sleep regex.

    The hint is a line number (1-based) from diagnose; we use it to
    prefer the matching line when multiple candidates exist, but the
    regex alone is the authority.
    """

    lines = text.splitlines(keepends=True)
    new_lines: list[str] = []
    removed = False
    for i, line in enumerate(lines, start=1):
        if _RANDOM_SLEEP_PATTERN.match(line):
            if hint is not None and not removed and i == int(hint):
                removed = True
                continue
            if hint is None:
                removed = True
                continue
            # hint mismatch — keep the line; we expect every F1 finding to
            # be 1:1 with a regex-matching line; if multiple match we
            # prefer the hinted index.
            new_lines.append(line)
            continue
        new_lines.append(line)
    if not removed:
        # No hint match — fall back to first regex hit anywhere.
        new_lines = []
        for line in lines:
            if not removed and _RANDOM_SLEEP_PATTERN.match(line):
                removed = True
                continue
            new_lines.append(line)
    return "".join(new_lines), removed


def _f3_xcconfig_target(project_root: pathlib.Path) -> pathlib.Path:
    """Return the Debug xcconfig that should receive F3's settings.

    REDACTED convention: ``REDACTED/Configurations/Project/Local/local-debug.xcconfig``
    is the live Debug xcconfig (referenced as
    ``baseConfigurationReference`` for the Debug configuration in the
    main pbxproj). This fixer is REDACTED-shaped for v1; future projects need
    a discovery pass via ``xcodebuild -showBuildSettings``.
    """

    candidates = [
        project_root
        / "REDACTED"
        / "Configurations"
        / "Project"
        / "Local"
        / "local-debug.xcconfig",
        project_root / "Configurations" / "Project" / "Local" / "local-debug.xcconfig",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ApplyError(
        "F3: could not locate local-debug.xcconfig under project root. "
        "v1 supports the REDACTED layout only; extend _f3_xcconfig_target() for "
        "other projects."
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
    """Stage + commit the given paths in their owning git tree.

    A path that lives inside a submodule is committed in the submodule
    first, then the submodule pointer update is committed in the parent.

    When ``no_verify`` is true, ``--no-verify`` is forwarded to ``git
    commit``. Used for throwaway test branches in foreign projects
    whose hooks are about code-review compliance rather than
    correctness; the orchestrator gates this behind a CLI flag.
    """

    commit_extra = ["--no-verify"] if no_verify else []
    submodule_paths: dict[pathlib.Path, list[str]] = {}
    parent_paths: list[str] = []

    for rel in rel_paths:
        abs_path = (project_root / rel).resolve()
        sub = _enclosing_submodule(project_root, abs_path)
        if sub is not None:
            sub_rel = str(abs_path.relative_to(sub))
            submodule_paths.setdefault(sub, []).append(sub_rel)
        else:
            parent_paths.append(rel)

    for sub, paths in submodule_paths.items():
        subprocess.check_call(["git", "-C", str(sub), "add", "--", *paths])
        subprocess.check_call(
            ["git", "-C", str(sub), "commit", *commit_extra, "-m", message]
        )
        sub_rel = str(sub.relative_to(project_root.resolve()))
        subprocess.check_call(["git", "-C", str(project_root), "add", "--", sub_rel])

    if parent_paths:
        subprocess.check_call(
            ["git", "-C", str(project_root), "add", "--", *parent_paths]
        )

    if submodule_paths or parent_paths:
        subprocess.check_call(
            ["git", "-C", str(project_root), "commit", *commit_extra, "-m", message]
        )


def _enclosing_submodule(
    project_root: pathlib.Path,
    abs_path: pathlib.Path,
) -> pathlib.Path | None:
    """Return the deepest submodule directory that contains abs_path, or None."""

    submodules = _list_submodule_paths(project_root)
    abs_resolved = abs_path.resolve()
    matches = [s for s in submodules if _is_inside(abs_resolved, s)]
    if not matches:
        return None
    return max(matches, key=lambda p: len(p.parts))


def _list_submodule_paths(project_root: pathlib.Path) -> list[pathlib.Path]:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(project_root), "submodule", "status", "--recursive"],
            text=True,
        )
    except subprocess.CalledProcessError:
        return []
    paths: list[pathlib.Path] = []
    for line in out.splitlines():
        # format: " <sha> <path> (<ref>)"
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        sub_path = (project_root / parts[1]).resolve()
        if sub_path.is_dir():
            paths.append(sub_path)
    return paths


def _is_inside(child: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _detect_submodule_changes(
    project_root: pathlib.Path,
    files_modified: list[str],
) -> list[SubmoduleChange]:
    """Return SubmoduleChange records for any submodule whose pointer moved.

    Compares the parent commit's tree against the immediate-prior commit
    to detect submodule pointer changes. Cheap; runs once per fixer.
    """

    out = subprocess.check_output(
        ["git", "-C", str(project_root), "diff", "--submodule=short", "HEAD~1", "HEAD"],
        text=True,
    )
    changes: list[SubmoduleChange] = []
    pattern = re.compile(
        r"^Submodule\s+(?P<path>\S+)\s+(?P<before>[0-9a-f]+)\.\.\.?(?P<after>[0-9a-f]+)"
    )
    for line in out.splitlines():
        m = pattern.match(line.strip())
        if m:
            changes.append(
                SubmoduleChange(
                    path=m.group("path"),
                    sha_before=m.group("before"),
                    sha_after=m.group("after"),
                )
            )
    return changes
