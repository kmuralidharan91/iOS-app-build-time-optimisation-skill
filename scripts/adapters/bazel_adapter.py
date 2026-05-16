"""Bazel adapter — measurement surface for ``ios-build-measure``.

Bazel iOS builds produce a JSON profile via ``bazelisk build --profile=<path>``;
this adapter wraps the invocation, captures wall-clock with ``time.monotonic``
around the subprocess, and returns a :class:`TimedBuild` shaped identically to
the xcode adapter's output (``result_bundle_path`` is ``None`` for Bazel — the
profile JSON path lives in ``stdout_log_path``'s sibling and is documented in
the per-run log for later critical-path attribution).

Argument mapping (the adapter signature is shared with xcode_adapter so
benchmark.py can call into either without branching):

* ``scheme`` -> Bazel target label, e.g. ``"//App:SmokeLib"``. The CLI exposes
  this as ``--scheme`` to keep parity with the xcode flow.
* ``configuration`` -> Bazel ``--config=<name>`` value, e.g. ``"ios_sim"``. The
  default is ``"ios_sim"`` because iOS-simulator is the v1 platform fence.
* ``destination`` -> ignored (xcodebuild-specific). A note is logged so users
  don't think the value flowed through.
* ``touch_file`` -> for ``kind=="incremental"``, ``touch_file.touch()`` exactly
  as xcode_adapter does; Bazel's file-content-hash invalidation will rebuild
  the touched compilation unit and everything downstream.

``show_build_settings``, ``script_phases``, and ``package_graph`` stay
``NotImplementedError`` in this release — they serve ``ios-build-diagnose``,
which short-circuits gracefully for non-Xcode build systems (see
``scripts/diagnose.py``). Bazel diagnose lands in v1.x once a smoke target
exercises ``bazel query --output=build`` and BUILD-file analyzers.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

from . import TimedBuild, require_ios


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


def _find_bazel_binary() -> str:
    """Return the path to a usable Bazel launcher.

    Preference order: ``bazelisk`` (auto-resolves the version pinned in
    ``.bazelversion``), then ``bazel``. Both are looked up on ``PATH``; we
    do not invoke a vendored binary because the launcher version drift
    between v1.x of this skill and the target project would mask real
    measurement issues.
    """

    for candidate in ("bazelisk", "bazel"):
        resolved = shutil.which(candidate)
        if resolved is not None:
            return resolved
    raise FileNotFoundError(
        "no `bazelisk` or `bazel` launcher found on PATH; install Bazelisk "
        "via `brew install bazelisk` (or equivalent) before running "
        "ios-build-measure against a Bazel project."
    )


def _run_clean(project_path: pathlib.Path, bazel_binary: str) -> None:
    """Best-effort ``bazel clean --expunge`` for a true clean build.

    ``--expunge`` wipes the entire output base, equivalent to
    ``xcodebuild clean`` + DerivedData removal. Failures are logged but
    not fatal — the subsequent build will still produce a wall-clock
    number, just one that may include some cached state.
    """

    subprocess.run(
        [bazel_binary, "clean", "--expunge"],
        cwd=project_path,
        check=False,
        capture_output=True,
    )


def _build_bazel_args(
    bazel_binary: str,
    scheme: str,
    configuration: str,
    profile_path: pathlib.Path,
    extra_args: list[str] | None,
) -> list[str]:
    """Construct the ``bazelisk build`` argv with profile capture.

    The target label is required; we surface it as ``scheme`` to keep
    benchmark.py's CLI shared across adapters.
    """

    if not scheme:
        raise ValueError(
            "Bazel measurement requires --scheme to be a Bazel target label "
            "(e.g. //App:SmokeLib); empty schemes are an xcode-only convenience."
        )

    args: list[str] = [bazel_binary, "build"]
    if configuration:
        args.append(f"--config={configuration}")
    args.append(f"--profile={profile_path}")
    args.append(scheme)
    if extra_args:
        args.extend(extra_args)
    return args


def _time_one_build(
    project_path: pathlib.Path,
    argv: list[str],
    log_path: pathlib.Path,
    kind: str,
) -> TimedBuild:
    """Run a single Bazel build and return the timing record."""

    log_path.parent.mkdir(parents=True, exist_ok=True)

    started_wall = datetime.now(timezone.utc).isoformat(timespec="seconds")
    started_mono = time.monotonic()
    with log_path.open("wb") as log_fh:
        completed = subprocess.run(
            argv,
            cwd=project_path,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            check=False,
        )
    duration = time.monotonic() - started_mono
    finished_wall = datetime.now(timezone.utc).isoformat(timespec="seconds")

    return TimedBuild(
        kind=kind,
        duration_seconds=duration,
        exit_code=completed.returncode,
        stdout_log_path=str(log_path),
        result_bundle_path=None,
        started_at=started_wall,
        finished_at=finished_wall,
    )


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
    """Run one Bazel build of the requested ``kind`` and return the timing.

    The signature mirrors :func:`xcode_adapter.measure` so benchmark.py can
    call either adapter without branching. ``destination`` is xcodebuild-
    specific and is ignored here (a note is printed once per run).
    ``extra_xcodebuild_args`` is forwarded to ``bazelisk build`` as extra args
    so users can pass e.g. ``--remote_cache=…`` through the same CLI knob.
    """

    require_ios(platform)
    if output_dir is None:
        raise ValueError("output_dir is required (used for log + profile paths)")
    if not scheme:
        raise ValueError(
            "Bazel measurement requires --scheme to be a Bazel target label "
            "(e.g. //App:SmokeLib)."
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    bazel_binary = _find_bazel_binary()

    log_path = output_dir / f"build-{kind}-{repeat_index}.log"
    profile_path = output_dir / f"build-{kind}-{repeat_index}-profile.json"

    if destination:
        print(
            f"[bazel-adapter] ignoring --destination={destination!r} "
            f"(xcodebuild-specific; Bazel selects platform via --config).",
            file=sys.stderr,
        )

    if kind == "clean":
        _run_clean(project_path, bazel_binary)
    elif kind == "incremental":
        if touch_file is None:
            raise ValueError(
                "incremental measurement requires --touch-file PATH; "
                "Bazel rebuilds the touched file's compilation unit + "
                "downstream targets while keeping the rest of the action "
                "cache warm."
            )
        if not touch_file.exists():
            raise FileNotFoundError(f"touch_file does not exist: {touch_file}")
        touch_file.touch()
    else:
        raise ValueError(
            f"unsupported kind={kind!r}; expected clean or incremental"
        )

    argv = _build_bazel_args(
        bazel_binary,
        scheme,
        configuration,
        profile_path,
        extra_xcodebuild_args,
    )

    return _time_one_build(project_path, argv, log_path, kind)


# --- diagnose surface (deferred to v1.x; intentional NotImplementedError) ---


def show_build_settings(*args, **kwargs):
    raise NotImplementedError(
        "Bazel `show_build_settings` is deferred to v1.x. "
        "ios-build-diagnose short-circuits non-Xcode build systems and emits "
        "a 'diagnose-incomplete' note rather than calling this method."
    )


def script_phases(*args, **kwargs):
    raise NotImplementedError(
        "Bazel `script_phases` is deferred to v1.x. "
        "ios-build-diagnose short-circuits non-Xcode build systems."
    )


def package_graph(*args, **kwargs):
    raise NotImplementedError(
        "Bazel `package_graph` is deferred to v1.x. "
        "ios-build-diagnose short-circuits non-Xcode build systems."
    )


def adapter_label() -> str:
    return "bazel"
