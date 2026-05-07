"""Script-phase analyzer — covers F1, F2, F3, F8 of the ground truth.

Reads the ``script_phases`` field of the DiagnosisContext (a list of
``ScriptPhase`` dataclasses produced by ``xcode_adapter.script_phases``)
and emits Findings against the WWDC22 110364 + Xcode 14 release-notes
recommendations on script-phase hygiene.

Rule ids:

- ``script-phase/random-sleep``           — F1, sleep $RANDOM in body
- ``script-phase/missing-debug-guard``    — F2, artifact-upload phases without CONFIGURATION check
- ``script-phase/missing-output-declarations`` — F3, no outputPaths declared
- ``script-phase/swiftlint-on-build``     — F8, SwiftLint as a build phase
"""

from __future__ import annotations

import pathlib
import re
from typing import Iterable

from . import (
    Citation,
    DiagnosisContext,
    Evidence,
    Finding,
    WallClockPrediction,
)


# Match `bash "${SRCROOT}"/path/Foo.sh`, `sh /abs/path/Foo.sh`, or a bare
# script path inside the phase body. Captures the trailing component
# so we can rglob() the project tree to find the file when ${SRCROOT}
# has not been expanded.
_INVOKED_SCRIPT_PATTERN = re.compile(
    r"""(?xi)
    (?:^|\s|;|&&|\|\|)
    (?:bash|sh|zsh|/bin/sh|/bin/bash)\s+
    "?(?P<path>[^"\s;]+\.sh)"?
    """,
)
_BARE_SCRIPT_PATTERN = re.compile(
    r"""(?xi)
    (?<![A-Za-z0-9_/\-])
    (?P<path>(?:\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|[^\s;"'`]+)
              (?:/[^\s;"'`]+)*?\.sh)
    (?![A-Za-z0-9_])
    """,
)


def _resolve_invoked_scripts(
    project_path: pathlib.Path,
    body: str,
) -> list[pathlib.Path]:
    """Return existing .sh files referenced from a phase body.

    Variables like ``${SRCROOT}`` and ``$SRCROOT`` cannot be expanded
    without ``xcodebuild`` so we strip the variable prefix and rglob
    the project tree for the trailing path. When more than one file
    matches the basename, all matches are returned (the analyzer rule
    runs on every body it finds).
    """

    candidates: list[str] = []
    for match in _INVOKED_SCRIPT_PATTERN.finditer(body):
        candidates.append(match.group("path"))
    for match in _BARE_SCRIPT_PATTERN.finditer(body):
        candidates.append(match.group("path"))

    resolved: list[pathlib.Path] = []
    seen: set[pathlib.Path] = set()
    for candidate in candidates:
        # Strip any ${VAR}/$VAR prefix and leading slashes so we have
        # a relative path to look for.
        cleaned = re.sub(r"\$\{[^}]+\}", "", candidate)
        cleaned = re.sub(r"\$[A-Za-z_][A-Za-z0-9_]*", "", cleaned)
        cleaned = cleaned.lstrip("/")
        if not cleaned:
            continue
        # Try a direct path first (in case it's already relative to
        # project root).
        direct = project_path / cleaned
        if direct.is_file():
            resolved.append(direct)
            seen.add(direct)
            continue
        # Otherwise rglob for the basename (safest for ${SRCROOT}-
        # rooted paths).
        basename = pathlib.PurePosixPath(cleaned).name
        for hit in project_path.rglob(basename):
            if "/Pods/" in str(hit):
                continue
            if "/DerivedData/" in str(hit):
                continue
            if hit in seen:
                continue
            seen.add(hit)
            resolved.append(hit)
    return resolved


def _read_script_body(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _build_extended_body(
    project_path: pathlib.Path,
    phase,
) -> tuple[str, list[pathlib.Path]]:
    """Return (combined_body, list_of_invoked_paths) for analyzer rules."""

    invoked = _resolve_invoked_scripts(project_path, phase.script)
    pieces: list[str] = [phase.script]
    for path in invoked:
        pieces.append("\n# --- inlined from " + str(path) + " ---\n")
        pieces.append(_read_script_body(path))
    return "\n".join(pieces), invoked


# ``sleep $RANDOM``, ``sleep $[ ( $RANDOM % 10 ) + 1 ]s``, ``sleep $((RANDOM % 5))``…
_RANDOM_SLEEP_PATTERN = re.compile(
    r"\bsleep\s+\$(?:\(\(|\[|\{)?\s*\(?\s*\$?\s*\(?\s*RANDOM\b",
    re.IGNORECASE,
)

# Heuristic: artifact-upload phases that almost never need to run on
# Debug simulator builds. Used by F2 (missing-debug-guard).
_ARTIFACT_UPLOAD_KEYWORDS = (
    "firebase",
    "crashlytics",
    "upload",
    "dsym",
    "fullstory",
    "datadog",
    "sentry",
    "bugsnag",
)

_SWIFTLINT_PATTERN = re.compile(r"\bswiftlint\b", re.IGNORECASE)


_APPLE_PARALLELIZATION_URL = "https://developer.apple.com/videos/play/wwdc2022/110364/"
_XCODE14_RELEASE_NOTES_URL = (
    "https://developer.apple.com/documentation/xcode-release-notes/xcode-14-release-notes"
)


def _line_for_match(body: str, match: re.Match[str]) -> int:
    """Return the 1-based line number of a regex match within body text."""

    return body.count("\n", 0, match.start()) + 1


def _phase_locator(phase) -> str:
    """Build a stable evidence path string for a phase: ``<target>/<phase name>``."""

    return f"{phase.target}/{phase.name}"


def _has_debug_guard(script_body: str) -> bool:
    """Return True when the script appears to early-exit on Debug builds.

    Looks for a ``CONFIGURATION`` reference plus a Debug literal /
    early exit. Matches ``[[ "$CONFIGURATION" == "Debug" ]] && exit``,
    ``if [[ $CONFIGURATION = Debug ]]; then exit``, etc. Conservative —
    a phase that mentions ``$CONFIGURATION`` without comparing it to
    Debug still counts as guarded (the user clearly knows about
    configuration-aware logic).
    """

    return "CONFIGURATION" in script_body or "$CONFIGURATION" in script_body


def _is_artifact_upload(phase) -> bool:
    """Heuristic: name OR body mentions an artifact-upload service."""

    haystack = (phase.name + "\n" + phase.script).lower()
    return any(keyword in haystack for keyword in _ARTIFACT_UPLOAD_KEYWORDS)


def _is_artifact_upload_extended(phase, extended_body: str) -> bool:
    """Same heuristic but checks the extended body (incl. invoked .sh files)."""

    haystack = (phase.name + "\n" + extended_body).lower()
    return any(keyword in haystack for keyword in _ARTIFACT_UPLOAD_KEYWORDS)


def run(context: DiagnosisContext) -> list[Finding]:
    findings: list[Finding] = []
    for phase in context.script_phases:
        findings.extend(_check_random_sleep(context.project_path, phase))
        findings.extend(_check_missing_debug_guard(context.project_path, phase))
        findings.extend(_check_missing_output_declarations(phase))
        findings.extend(_check_swiftlint_on_build(context.project_path, phase))
    return findings


def _scan_with_invoked(
    project_path: pathlib.Path,
    phase,
    pattern: re.Pattern[str],
) -> tuple[re.Match[str] | None, str, str]:
    """Search phase body, then each invoked .sh file, until a match hits.

    Returns ``(match, body_searched, evidence_path)`` where
    ``evidence_path`` is the phase locator if the match was inline, or
    the absolute path of the invoked script otherwise. Returns
    ``(None, "", "")`` when no match is found.
    """

    inline_match = pattern.search(phase.script)
    if inline_match is not None:
        return inline_match, phase.script, _phase_locator(phase)

    for invoked_path in _resolve_invoked_scripts(project_path, phase.script):
        body = _read_script_body(invoked_path)
        match = pattern.search(body)
        if match is not None:
            return match, body, str(invoked_path)
    return None, "", ""


def _check_random_sleep(
    project_path: pathlib.Path,
    phase,
) -> Iterable[Finding]:
    match, body, evidence_path = _scan_with_invoked(
        project_path, phase, _RANDOM_SLEEP_PATTERN
    )
    if match is None:
        return ()
    yield Finding(
        rule_id="script-phase/random-sleep",
        family="script-phase",
        title=f"Random `sleep` in script phase '{phase.name}' on every build",
        evidence=Evidence(
            kind="file_line",
            path=evidence_path,
            line=_line_for_match(body, match),
            raw=body[max(0, match.start() - 40): match.end() + 40].strip(),
        ),
        impact_category="high",
        wall_clock_predicted=WallClockPrediction(
            method="heuristic",
            estimate_seconds=5.0,
            min_seconds=1.0,
            max_seconds=10.0,
            notes="Random sleep magnitude depends on the literal $RANDOM bound.",
        ),
        citation=Citation(
            url=_APPLE_PARALLELIZATION_URL,
            source="WWDC22 110364 — Demystify parallelization in Xcode builds",
        ),
        source_method="pbxproj-parse",
        notes=(
            "Sleeps inside a build phase block downstream parallel work "
            "for their full duration; the cost is the same on Debug, "
            "InHouse, and Distribution.",
        ),
    )


def _check_missing_debug_guard(
    project_path: pathlib.Path,
    phase,
) -> Iterable[Finding]:
    extended_body, invoked = _build_extended_body(project_path, phase)
    if not _is_artifact_upload_extended(phase, extended_body):
        return ()
    if _has_debug_guard(extended_body):
        return ()
    evidence_path = _phase_locator(phase)
    if invoked:
        evidence_path = str(invoked[0])
    yield Finding(
        rule_id="script-phase/missing-debug-guard",
        family="script-phase",
        title=(
            f"Artifact-upload phase '{phase.name}' has no Debug early-exit guard"
        ),
        evidence=Evidence(
            kind="file_line",
            path=evidence_path,
            raw=extended_body.strip()[:500],
        ),
        impact_category="medium",
        wall_clock_predicted=WallClockPrediction(
            method="measured-on-private-corpus",
            estimate_seconds=3.0,
            min_seconds=2.0,
            max_seconds=4.0,
            notes=(
                "TODO(public-cite: NetNewsWire) confirm: Crashlytics and "
                "dSYM-upload phases run on every Debug build on the "
                "private corpus; gating them with $CONFIGURATION="
                "\"Debug\" early-exit recovers the run time."
            ),
        ),
        citation=Citation(
            url=_APPLE_PARALLELIZATION_URL,
            source="WWDC22 110364 — Demystify parallelization in Xcode builds",
        ),
        source_method="pbxproj-parse",
        notes=(
            "Heuristic: phase name or body mentions one of "
            f"{', '.join(_ARTIFACT_UPLOAD_KEYWORDS)}.",
        ),
    )


def _check_missing_output_declarations(phase) -> Iterable[Finding]:
    if phase.output_paths:
        return ()
    # F3 specifically calls out the case where there are no input AND
    # no output paths. We surface even the input-only case, but mark
    # the impact category lower if alwaysOutOfDate is True (the user
    # has already opted into "run every build").
    impact: str = "high" if not phase.always_out_of_date else "medium"
    yield Finding(
        rule_id="script-phase/missing-output-declarations",
        family="script-phase",
        title=(
            f"Script phase '{phase.name}' on target {phase.target!s} declares "
            "no output paths"
        ),
        evidence=Evidence(
            kind="file_line",
            path=_phase_locator(phase),
            value=(
                f"input_paths={list(phase.input_paths)!r} "
                f"output_paths={list(phase.output_paths)!r} "
                f"always_out_of_date={phase.always_out_of_date}"
            ),
        ),
        impact_category=impact,
        wall_clock_predicted=WallClockPrediction(
            method="measured-on-private-corpus",
            estimate_seconds=4.0,
            min_seconds=3.0,
            max_seconds=5.0,
            notes=(
                "TODO(public-cite: NetNewsWire) confirm magnitude: phases "
                "without output declarations defeat Xcode's 'run only "
                "when inputs change' optimisation; estimate is per-build "
                "incremental cost."
            ),
        ),
        citation=Citation(
            url=_APPLE_PARALLELIZATION_URL,
            source="WWDC22 110364 — Demystify parallelization in Xcode builds",
        ),
        source_method="pbxproj-parse",
        notes=(
            "Declaring outputPaths lets the build system mark the phase "
            "up-to-date when its inputs are unchanged.",
        ),
    )


def _check_swiftlint_on_build(
    project_path: pathlib.Path,
    phase,
) -> Iterable[Finding]:
    extended_body, invoked = _build_extended_body(project_path, phase)
    haystack = phase.name + "\n" + extended_body
    if not _SWIFTLINT_PATTERN.search(haystack):
        return ()
    evidence_path = _phase_locator(phase)
    if invoked and not _SWIFTLINT_PATTERN.search(phase.script):
        # Match was inside an invoked .sh file; point evidence there.
        for invoked_path in invoked:
            if _SWIFTLINT_PATTERN.search(_read_script_body(invoked_path)):
                evidence_path = str(invoked_path)
                break
    yield Finding(
        rule_id="script-phase/swiftlint-on-build",
        family="script-phase",
        title=f"SwiftLint runs as a build phase ('{phase.name}') on every build",
        evidence=Evidence(
            kind="file_line",
            path=evidence_path,
            raw=extended_body.strip()[:500],
        ),
        impact_category="low",
        wall_clock_predicted=WallClockPrediction(
            method="heuristic",
            estimate_seconds=2.0,
            min_seconds=1.0,
            max_seconds=6.0,
            notes=(
                "SwiftLint as a build phase blocks the compile pipeline "
                "for its full duration. Pre-commit hook + editor-on-save "
                "recovers the time without losing enforcement."
            ),
        ),
        citation=Citation(
            url=_APPLE_PARALLELIZATION_URL,
            source="WWDC22 110364 — Demystify parallelization in Xcode builds",
        ),
        source_method="pbxproj-parse",
        notes=(),
    )
