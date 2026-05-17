"""Tuist adapter — measurement + diagnose surface for the build skills.

Tuist generates a stock ``*.xcworkspace`` (and ``*.xcodeproj``) from
``Project.swift`` and then delegates to ``xcodebuild`` for the actual
build. So this adapter:

1. Owns the ``tuist generate`` lifecycle. Regenerates on every
   ``measure()`` so the workspace reflects the current ``Project.swift``
   state — this matches the Tuist user experience where ``tuist
   generate`` is part of the build loop, and Tuist's own caching
   keeps the regeneration near-free on unchanged manifests.
2. Delegates timing capture to ``xcode_adapter.measure`` against the
   generated workspace. xcode_adapter's ``find_workspace_or_project``
   picks up the ``*.xcworkspace`` Tuist produces, so no path
   translation is needed.
3. Delegates ``show_build_settings`` / ``script_phases`` /
   ``package_graph`` to ``xcode_adapter`` for the same reason — the
   generated ``project.pbxproj``, ``Package.resolved``, and
   ``Package.swift`` files are real Xcode artefacts that the existing
   parsers handle unchanged.

Bench parity: development-time wikipedia-ios Tuist measurements
followed the same ``tuist generate && xcodebuild`` flow; v1.3 ships
this adapter so ``ios-build-doctor`` can do the same automatically.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

from . import PackageGraph, ScriptPhase, TimedBuild, require_ios


def detect(project_path: pathlib.Path) -> bool:
    """Return True when the project root carries a Tuist manifest."""
    return (project_path / "Project.swift").is_file()


def _tuist_argv_prefix() -> list[str]:
    """Return the argv prefix needed to invoke ``tuist``.

    Resolution order:

    1. ``tuist`` on PATH — return ``["tuist"]``. This covers Homebrew
       installs and any environment where the user has explicitly put
       the tuist binary directory on PATH.
    2. ``mise`` on PATH — return ``["mise", "exec", "--", "tuist"]``.
       mise honours the ``.mise.toml`` in the cwd, so when this prefix
       runs inside a Tuist project root it auto-resolves the pinned
       version (the smoke target at ``tests/tuist-smoke-ios/`` pins
       4.191.5 via ``.mise.toml``).
    3. Neither — raise FileNotFoundError with install hints.
    """

    if shutil.which("tuist"):
        return ["tuist"]
    if shutil.which("mise"):
        return ["mise", "exec", "--", "tuist"]
    raise FileNotFoundError(
        "tuist not found on PATH and mise not available. Install via "
        "mise (https://mise.jdx.dev) or Homebrew (`brew install tuist`); "
        "the smoke target at tests/tuist-smoke-ios/ pins 4.191.5 via "
        ".mise.toml so `mise install` provisions a known-good version."
    )


def _run_tuist_generate(project_path: pathlib.Path) -> None:
    """Invoke ``tuist generate --no-open`` to materialise the workspace.

    Tuist generation is incremental; re-running on an unchanged
    ``Project.swift`` finishes in ~1.5 s. We always regenerate before
    a build so the workspace reflects the current manifest state, which
    matches the Tuist user experience and keeps measurement honest.
    """

    argv = [*_tuist_argv_prefix(), "generate", "--no-open"]
    completed = subprocess.run(
        argv,
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"tuist generate failed (exit {completed.returncode}): "
            f"{completed.stderr.strip()[:500]}"
        )


def _ensure_workspace_exists(project_path: pathlib.Path) -> None:
    """Regenerate the Tuist workspace if it's missing on disk.

    Used by the diagnose-surface methods (``show_build_settings``,
    ``script_phases``, ``package_graph``) so they don't crash when the
    user runs ``ios-build-diagnose`` against a fresh checkout that
    hasn't been generated yet.
    """

    workspaces = list(project_path.glob("*.xcworkspace"))
    if workspaces:
        return
    _run_tuist_generate(project_path)


def measure(
    project_path: pathlib.Path,
    scheme: str | None,
    configuration: str,
    destination: str,
    platform: str = "ios",
    touch_file: pathlib.Path | None = None,
    kind: str = "clean",
    output_dir: pathlib.Path | None = None,
    repeat_index: int = 0,
    extra_xcodebuild_args: list[str] | None = None,
) -> TimedBuild:
    """Run one Tuist-orchestrated build of the requested ``kind``.

    Flow:

    1. ``tuist generate --no-open`` to refresh the workspace.
    2. Delegate to :func:`xcode_adapter.measure` against the generated
       workspace. The xcodebuild invocation, log capture, ``.xcresult``
       bundle path, and ``time.monotonic`` wall-clock are unchanged
       from the Xcode path — the only Tuist-specific step is the
       generate call above.

    ``tuist generate`` time is **excluded** from the measured wall-clock
    because it represents the IDE-side cost (Xcode users don't pay it).
    Including it would bias Tuist vs Xcode comparisons; the measured
    delta is the xcodebuild portion only, which is what's comparable.
    """

    require_ios(platform)
    if output_dir is None:
        raise ValueError("output_dir is required (used for log + xcresult paths)")
    output_dir.mkdir(parents=True, exist_ok=True)

    _run_tuist_generate(project_path)

    from . import xcode_adapter  # noqa: PLC0415 — local import keeps the module optional
    return xcode_adapter.measure(
        project_path=project_path,
        scheme=scheme,
        configuration=configuration,
        destination=destination,
        platform=platform,
        touch_file=touch_file,
        kind=kind,
        output_dir=output_dir,
        repeat_index=repeat_index,
        extra_xcodebuild_args=extra_xcodebuild_args,
    )


def show_build_settings(
    project_path: pathlib.Path,
    scheme: str | None,
    configuration: str,
    platform: str = "ios",
) -> dict[str, str]:
    """Delegate to ``xcode_adapter.show_build_settings`` post-generate.

    Tuist's generated workspace is a real ``*.xcworkspace`` that
    ``xcodebuild -showBuildSettings -json`` resolves correctly. We
    ensure the workspace exists first (generating if needed) so the
    diagnose step works against a fresh checkout.
    """

    require_ios(platform)
    _ensure_workspace_exists(project_path)
    from . import xcode_adapter  # noqa: PLC0415
    return xcode_adapter.show_build_settings(
        project_path, scheme, configuration, platform,
    )


def script_phases(
    project_path: pathlib.Path,
    platform: str = "ios",
) -> list[ScriptPhase]:
    """Delegate to ``xcode_adapter.script_phases`` post-generate.

    Tuist generates a real ``project.pbxproj`` with
    ``PBXShellScriptBuildPhase`` entries (Project.swift's ``scripts:``
    DSL compiles down to the same pbxproj structure), so the Xcode
    pbxproj walker handles them unchanged.
    """

    require_ios(platform)
    _ensure_workspace_exists(project_path)
    from . import xcode_adapter  # noqa: PLC0415
    return xcode_adapter.script_phases(project_path, platform)


def package_graph(
    project_path: pathlib.Path,
    platform: str = "ios",
) -> PackageGraph:
    """Delegate to ``xcode_adapter.package_graph`` post-generate.

    Tuist projects use the same ``Package.swift`` / ``Package.resolved``
    artefacts as stock Xcode projects, so the Xcode SPM walker returns
    the same shapes.
    """

    require_ios(platform)
    _ensure_workspace_exists(project_path)
    from . import xcode_adapter  # noqa: PLC0415
    return xcode_adapter.package_graph(project_path, platform)


def adapter_label() -> str:
    """Identifier used in benchmark artefact ``project.build_system``."""
    return "tuist"


if __name__ == "__main__":  # pragma: no cover — module is library-first
    print("tuist_adapter: import as scripts.adapters.tuist_adapter", file=sys.stderr)
    raise SystemExit(2)
