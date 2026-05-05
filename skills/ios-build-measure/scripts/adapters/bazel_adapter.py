"""Bazel adapter — skeleton for Phase A, full impl deferred.

Bazel iOS builds produce a JSON profile via ``bazelisk build
--profile=<path>``; the eventual measure() implementation will parse
the chrome-trace-style flow events to derive a per-target wall-clock
DAG (https://bazel.build/advanced/performance/json-trace-profile).

Phase A ships detect() so detect_build_system works on Bazel projects;
measure() raises NotImplementedError until a Bazel-shaped smoke target
is on disk.
"""

from __future__ import annotations

import pathlib

from . import require_ios


def detect(project_path: pathlib.Path) -> bool:
    """Return True when ``project_path`` carries Bazel root + at least one BUILD."""
    has_root = (
        (project_path / "MODULE.bazel").is_file()
        or (project_path / "WORKSPACE").is_file()
        or (project_path / "WORKSPACE.bazel").is_file()
    )
    if not has_root:
        return False
    for build_name in ("BUILD", "BUILD.bazel"):
        for _ in project_path.rglob(build_name):
            return True
    return False


def measure(*args, **kwargs):  # noqa: D401 — stub
    require_ios(kwargs.get("platform", "ios"))
    raise NotImplementedError(
        "Bazel measurement deferred — Phase A ships detect() only. "
        "Wikipedia iOS Bazel checkout is the eventual smoke target "
        "(docs/PLAN.md row 1 + 'Multi-system parity gate'). "
        "Implementation will use bazelisk build --profile=<json> and "
        "parse the chrome-trace-style flow events."
    )


def show_build_settings(*args, **kwargs):
    raise NotImplementedError("Bazel diagnose ships in Phase A or later.")


def script_phases(*args, **kwargs):
    raise NotImplementedError("Bazel diagnose ships in Phase A or later.")


def package_graph(*args, **kwargs):
    raise NotImplementedError("Bazel diagnose ships in Phase A or later.")


def adapter_label() -> str:
    return "bazel"
