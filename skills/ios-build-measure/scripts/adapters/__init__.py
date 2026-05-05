"""Adapter package for ios-build-measure / diagnose / fix.

Three adapters live alongside this module: xcode_adapter, tuist_adapter,
bazel_adapter. The detection function below picks one based on on-disk
signals; tie-breaker order is Tuist > Bazel > Xcode (a project may have
both a Tuist manifest and a generated *.xcodeproj, in which case Tuist
is the source of truth — see AGENTS.md "Build-system adapter contract").

v1 ships iOS only. The platform parameter on adapter APIs is enforced as
"ios"; other values raise ValueError so v2 (macOS / watchOS / tvOS /
visionOS) can be added additively without breaking the v1 contract.
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any, Literal


SupportedPlatform = Literal["ios"]
KnownBuildSystem = Literal["xcode", "tuist", "bazel"]


@dataclasses.dataclass(frozen=True)
class TimedBuild:
    """One xcodebuild / tuist build / bazelisk build invocation that completed.

    Holds the wall-clock duration in seconds, exit code, the kind label
    (clean / incremental), the path to the captured stdout log, and the
    optional path to a result-bundle (xcresult) on Xcode/Tuist runs.
    """

    kind: str  # "clean" or "incremental"
    duration_seconds: float
    exit_code: int
    stdout_log_path: str
    result_bundle_path: str | None = None
    started_at: str = ""  # ISO-8601 UTC
    finished_at: str = ""  # ISO-8601 UTC


@dataclasses.dataclass(frozen=True)
class TypeSummary:
    """Roll-up across all repeats of one build kind ("clean" or "incremental")."""

    count: int
    min_seconds: float
    max_seconds: float
    median_seconds: float
    mean_seconds: float
    spread_seconds: float
    spread_percent: float
    high_variance: bool


@dataclasses.dataclass(frozen=True)
class CriticalPathNode:
    target: str
    duration_seconds: float
    depth: int
    dominant_task: str
    predecessors: list[str]


@dataclasses.dataclass(frozen=True)
class CriticalPath:
    method: str | None  # "xcresult-target-graph" | "timing-summary-aggregate" | None
    nodes: list[CriticalPathNode]
    longest_chain_seconds: float
    notes: list[str]


@dataclasses.dataclass(frozen=True)
class ScriptPhase:
    """One ``PBXShellScriptBuildPhase`` extracted from a project.pbxproj.

    Attribute names match those the analyzers in
    ``scripts/analyzers/script_phase.py`` already read (``phase.target``,
    ``phase.name``, ``phase.script``, ``phase.input_paths``,
    ``phase.output_paths``, ``phase.always_out_of_date``).
    """

    target: str
    name: str
    script: str
    input_paths: tuple[str, ...]
    output_paths: tuple[str, ...]
    always_out_of_date: bool
    pbxproj_path: str = ""


@dataclasses.dataclass(frozen=True)
class Pin:
    """One entry from a Package.resolved file.

    ``name`` corresponds to Package.resolved's ``identity`` field (the
    canonical SPM identity). ``source_resolved_path`` is the absolute
    path of the Package.resolved file the pin was read from; the
    analyzer surfaces it as the evidence path.
    """

    name: str
    version: str | None
    revision: str | None
    branch: str | None
    location: str
    source_resolved_path: str


@dataclasses.dataclass(frozen=True)
class LocalModule:
    """One local SPM module discovered under the project tree.

    ``source_count`` is the count of ``*.swift`` files under the
    module's source roots (typically ``Sources/<target>/``); the
    oversized-module rule fires when it crosses the threshold in
    ``references/defaults.md``.
    """

    name: str
    path: str
    source_count: int


@dataclasses.dataclass(frozen=True)
class PackageGraph:
    """Aggregate SPM state read from a project tree.

    ``pins`` is the union of every pin in every reachable
    Package.resolved (workspace-level + per-package), de-duplicated by
    ``(name, source_resolved_path)``. ``local_modules`` is the list
    of local Package.swift modules with their swift-file counts.
    """

    pins: tuple[Pin, ...]
    local_modules: tuple[LocalModule, ...]


def detect_build_system(project_path: pathlib.Path) -> KnownBuildSystem:
    """Detect which build system drives the project at ``project_path``.

    Detection rules (per AGENTS.md, verified against current Tuist/Bazel
    docs in Phase A):

    * **Tuist** when ``Project.swift`` exists at the project root. Tuist's
      manifest guide names this as the required manifest file
      (https://docs.tuist.dev/en/guides/features/projects/manifests).
    * **Bazel** when ``MODULE.bazel`` (Bzlmod) or ``WORKSPACE`` /
      ``WORKSPACE.bazel`` (legacy) exists at the project root, and at
      least one ``BUILD`` / ``BUILD.bazel`` file exists anywhere under
      it (https://bazel.build/docs/bazel-and-apple).
    * **Xcode** when a ``*.xcodeproj`` and/or ``*.xcworkspace`` exists
      and neither Tuist nor Bazel signals are present.

    Tie-breaker — Tuist > Bazel > Xcode. A project may have both a Tuist
    manifest and a generated ``*.xcodeproj``; the manifest is the source
    of truth so we return ``"tuist"``. Same idea for Bazel projects with
    generated Xcode wrappers.
    """

    if not project_path.is_dir():
        raise ValueError(f"project_path is not a directory: {project_path}")

    if (project_path / "Project.swift").is_file():
        return "tuist"

    has_module_bazel = (project_path / "MODULE.bazel").is_file()
    has_workspace_bazel = (
        (project_path / "WORKSPACE").is_file()
        or (project_path / "WORKSPACE.bazel").is_file()
    )
    if has_module_bazel or has_workspace_bazel:
        for build_file_name in ("BUILD", "BUILD.bazel"):
            for _ in project_path.rglob(build_file_name):
                return "bazel"
        # MODULE/WORKSPACE without BUILD files — fall through; this is
        # an incomplete Bazel checkout, treat it as not-Bazel until the
        # user adds at least one BUILD file.

    has_xcodeproj = any(project_path.glob("*.xcodeproj"))
    has_xcworkspace = any(project_path.glob("*.xcworkspace"))
    if has_xcodeproj or has_xcworkspace:
        return "xcode"

    raise RuntimeError(
        f"could not detect build system at {project_path}: no Project.swift, "
        f"no MODULE.bazel/WORKSPACE+BUILD files, and no *.xcodeproj/*.xcworkspace "
        f"found at the project root."
    )


def load_adapter(build_system: KnownBuildSystem) -> Any:
    """Return the adapter module for the named build system.

    Importing inside the function keeps adapter modules optional at
    import time (a developer running benchmark.py against an Xcode
    project doesn't pay for the Tuist/Bazel adapters).
    """

    if build_system == "xcode":
        from . import xcode_adapter  # noqa: PLC0415
        return xcode_adapter
    if build_system == "tuist":
        from . import tuist_adapter  # noqa: PLC0415
        return tuist_adapter
    if build_system == "bazel":
        from . import bazel_adapter  # noqa: PLC0415
        return bazel_adapter
    raise ValueError(f"unknown build system: {build_system!r}")


def require_ios(platform: str) -> SupportedPlatform:
    """Enforce the v1 platform fence. macOS/watchOS/tvOS/visionOS land in v2."""

    if platform == "ios":
        return "ios"
    raise ValueError(
        f"platform={platform!r} is not supported in v1. "
        f"v1 ships iOS only; macOS/watchOS/tvOS/visionOS arrive in v2 — "
        f"see docs/PLAN.md 'Platform scope and roadmap'."
    )
