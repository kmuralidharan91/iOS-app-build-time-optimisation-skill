"""Tuist adapter — skeleton; full impl deferred.

Tuist generates a stock ``*.xcodeproj`` from ``Project.swift`` and then
shells out to ``xcodebuild``, so when the time comes this adapter will
delegate timing parsing to xcode_adapter while owning the
``tuist generate`` / ``tuist clean`` / ``tuist build`` lifecycle.

v1 ships detect() so detect_build_system works on Tuist projects;
measure() raises NotImplementedError until a Tuist-shaped smoke target
is on disk (see docs/PLAN.md: Wikipedia iOS Tuist checkout).
"""

from __future__ import annotations

import pathlib

from . import require_ios


def detect(project_path: pathlib.Path) -> bool:
    """Return True when the project root carries a Tuist manifest."""
    return (project_path / "Project.swift").is_file()


def measure(*args, **kwargs):  # noqa: D401 — stub
    require_ios(kwargs.get("platform", "ios"))
    raise NotImplementedError(
        "Tuist measurement deferred — v1 ships detect() only. "
        "Wikipedia iOS Tuist checkout is the eventual smoke target "
        "(docs/PLAN.md 'Multi-system parity gate'). "
        "Implementation will delegate to xcode_adapter for timing parsing."
    )


def show_build_settings(*args, **kwargs):
    raise NotImplementedError("Tuist diagnose deferred to a later phase.")


def script_phases(*args, **kwargs):
    raise NotImplementedError("Tuist diagnose deferred to a later phase.")


def package_graph(*args, **kwargs):
    raise NotImplementedError("Tuist diagnose deferred to a later phase.")


def adapter_label() -> str:
    return "tuist"
