#!/usr/bin/env python3
"""ios-build-diagnose CLI — surface ranked findings on an iOS project.

Reads on-disk project state (Xcode adapter only in v1) plus a benchmark
``measurement.json`` and emits a JSON artifact conforming to
``schemas/diagnosis.schema.json``. Every finding carries a rule id,
evidence, wall-clock impact category, and an Apple/WWDC/Tuist/Bazel
citation per AGENTS.md non-negotiable principle 4.

PR-#1 effective-settings logic and PR-#2 sandboxing/fuse audit are
baked in via the build_setting analyzer. PR-#2 outputs land in
``additional_recommendations[]`` rather than ``findings[]`` so the
F1–F9 ground-truth recall denominator stays unambiguous.

Usage:

    python3 scripts/diagnose.py \
        --project-path /path/to/project-root \
        --scheme Debug --configuration Debug \
        --measurement-artifact docs/smoke/1/measurement.json \
        --output-dir docs/smoke/2/

The orchestrator never invokes ``xcodebuild build``. It only runs
``xcodebuild -showBuildSettings -json`` (read-only) and reads pbxproj
plists + Package.resolved files from disk.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

# Allow running as ``python3 scripts/diagnose.py`` from the repo root
# without having to install the package.
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    sys.path.insert(0, str(_REPO_ROOT))

from adapters import PackageGraph, detect_build_system  # noqa: E402
from adapters import xcode_adapter  # noqa: E402
from analyzers import (  # noqa: E402
    DiagnosisContext,
    Finding,
    Recommendation,
    to_finding_dict,
)
from analyzers import asset_catalog as asset_catalog_analyzer  # noqa: E402
from analyzers import build_setting as build_setting_analyzer  # noqa: E402
from analyzers import script_phase as script_phase_analyzer  # noqa: E402
from analyzers import spm_graph as spm_graph_analyzer  # noqa: E402


_TOOL_NAME = "ios-build-diagnose"
_TOOL_VERSION = "0.1.0"


def _impact_rank(category: str) -> int:
    return {"high": 0, "medium": 1, "low": 2, "unknown": 3}.get(category, 4)


def _rank_findings(findings: list[Finding]) -> list[Finding]:
    """Rank by impact category then by predicted estimate seconds (desc)."""

    def sort_key(finding: Finding) -> tuple[int, float]:
        estimate = finding.wall_clock_predicted.estimate_seconds or 0.0
        return (_impact_rank(finding.impact_category), -estimate)

    return sorted(findings, key=sort_key)


def _git_sha(project_path: pathlib.Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _git_branch(project_path: pathlib.Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_path), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        branch = completed.stdout.strip()
        if branch != "HEAD":
            return branch
        # Detached HEAD: fall back to a remote-tracking ref pointing at
        # the same commit (mirrors scripts/benchmark.py).
        sha = _git_sha(project_path)
        if not sha:
            return ""
        completed = subprocess.run(
            [
                "git", "-C", str(project_path), "for-each-ref",
                "--points-at", sha,
                "--format=%(refname:short)",
                "refs/remotes/",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        for ref in completed.stdout.splitlines():
            ref = ref.strip()
            if not ref or ref.endswith("/HEAD"):
                continue
            if "/" in ref:
                return ref.split("/", 1)[1]
        return ""
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _load_measurement(path: pathlib.Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"[diagnose] could not read --measurement-artifact {path}: {exc}",
            file=sys.stderr,
        )
        return None
    if isinstance(payload, dict):
        # Stash the source path so analyzers can reference it in evidence.
        payload["_artifact_path"] = str(path)
        return payload
    return None


def _load_resolved_settings_dump(path: pathlib.Path) -> dict[str, str]:
    """Load a pre-captured xcodebuild -showBuildSettings -json dump.

    Accepts the live ``xcodebuild`` JSON shape (a list of
    ``{target, action, buildSettings}`` objects); merges target
    settings the same way the live adapter does so analyzers see one
    flat dict.
    """

    payload = json.loads(path.read_text(encoding="utf-8"))
    merged: dict[str, str] = {}
    if isinstance(payload, list):
        for entry in payload:
            settings = entry.get("buildSettings", {}) if isinstance(entry, dict) else {}
            for key, value in settings.items():
                merged[str(key)] = str(value)
    elif isinstance(payload, dict):
        merged = {str(k): str(v) for k, v in payload.items()}
    return merged


def _build_context(
    project_path: pathlib.Path,
    scheme: str | None,
    configuration: str,
    platform: str,
    measurement: dict[str, Any] | None,
    skip_xcodebuild: bool,
    resolved_settings_json: pathlib.Path | None,
) -> tuple[DiagnosisContext, list[str]]:
    notes: list[str] = []

    build_system = detect_build_system(project_path)
    if build_system != "xcode":
        # Bazel measurement ships in v1; Bazel diagnose (BUILD-file phase
        # analyzers, bazel query for resolved settings, package_graph from
        # the rules_swift_package_manager pin lockfile) lands in v1.x. For
        # now, return an empty diagnosis context so the downstream
        # analyzers all run and emit zero findings rather than crashing on
        # an xcode_adapter call. The "diagnose-incomplete" note flows into
        # the transcript so users know the analysis was a no-op.
        notes.append(
            f"adapter={build_system!r} not yet wired for diagnose; v1 ships "
            f"Xcode-only diagnose. Bazel-side findings (BUILD script phases, "
            f"bazel query --output=build, package_graph from rules_swift_"
            f"package_manager pins) land in v1.x — see references/defaults.md "
            f"'Roadmap'."
        )
        context = DiagnosisContext(
            project_path=project_path,
            scheme=scheme,
            configuration=configuration,
            platform=platform,
            measurement=measurement,
            resolved_settings={},
            script_phases=[],
            package_graph=PackageGraph(pins=(), local_modules=()),
        )
        return context, notes

    if resolved_settings_json is not None:
        resolved = _load_resolved_settings_dump(resolved_settings_json)
        notes.append(
            "Loaded resolved build settings from "
            f"{resolved_settings_json}; live xcodebuild not invoked."
        )
    elif skip_xcodebuild:
        resolved = {}
        notes.append(
            "--skip-xcodebuild was set; build-setting findings (F4, F9) "
            "and PR-#2 recommendations short-circuit."
        )
    else:
        resolved = xcode_adapter.show_build_settings(
            project_path,
            scheme=scheme,
            configuration=configuration,
            platform=platform,
        )
        if not resolved:
            notes.append(
                "xcodebuild -showBuildSettings returned nothing (timeout, "
                "missing binary, network down, or non-JSON output); build-"
                "setting findings short-circuit. Re-run with VPN up or "
                "pass --resolved-settings-json PATH to exercise F4 / F9 / "
                "sandboxing / fuse rules."
            )

    phases = xcode_adapter.script_phases(project_path, platform=platform)
    package_graph_value = xcode_adapter.package_graph(project_path, platform=platform)

    context = DiagnosisContext(
        project_path=project_path,
        scheme=scheme,
        configuration=configuration,
        platform=platform,
        measurement=measurement,
        resolved_settings=resolved,
        script_phases=phases,
        package_graph=package_graph_value,
    )
    return context, notes


def _summary(
    findings: list[Finding],
    recommendations: list[Recommendation],
) -> dict[str, Any]:
    by_impact: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    by_family: dict[str, int] = {}
    for f in findings:
        by_impact[f.impact_category] = by_impact.get(f.impact_category, 0) + 1
        by_family[f.family] = by_family.get(f.family, 0) + 1
    return {
        "total_findings": len(findings),
        "total_additional_recommendations": len(recommendations),
        "by_impact": by_impact,
        "by_family": by_family,
    }


def diagnose(
    project_path: pathlib.Path,
    scheme: str | None,
    configuration: str,
    destination: str,
    platform: str,
    measurement_artifact: pathlib.Path | None,
    output_dir: pathlib.Path,
    skip_xcodebuild: bool,
    resolved_settings_json: pathlib.Path | None,
) -> dict[str, Any]:
    """Run the diagnose pipeline and return the artifact dict."""

    output_dir.mkdir(parents=True, exist_ok=True)

    measurement = _load_measurement(measurement_artifact)

    context, top_notes = _build_context(
        project_path,
        scheme,
        configuration,
        platform,
        measurement,
        skip_xcodebuild,
        resolved_settings_json,
    )

    findings: list[Finding] = []
    findings.extend(script_phase_analyzer.run(context))
    bs_findings, bs_recommendations = build_setting_analyzer.run(context)
    findings.extend(bs_findings)
    findings.extend(asset_catalog_analyzer.run(context))
    findings.extend(spm_graph_analyzer.run(context))

    findings = _rank_findings(findings)

    artifact: dict[str, Any] = {
        "schema_version": "1.0.0",
        "tool": {"name": _TOOL_NAME, "version": _TOOL_VERSION},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": {
            "path": str(project_path),
            "build_system": "xcode",
            "git_sha": _git_sha(project_path),
            "git_branch": _git_branch(project_path),
            "platform": platform,
        },
        "configuration": {
            "scheme": scheme,
            "configuration": configuration,
            "destination": destination,
        },
        "inputs": {
            "measurement_artifact_path": (
                str(measurement_artifact) if measurement_artifact else None
            ),
        },
        "findings": [to_finding_dict(f) for f in findings],
        "additional_recommendations": [
            to_finding_dict(r) for r in bs_recommendations
        ],
        "summary": _summary(findings, bs_recommendations),
        "notes": top_notes,
    }
    return artifact


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ios-build-diagnose",
        description=(
            "Surface ranked iOS build-time findings — script phases, build "
            "settings, asset catalogs, SPM graph — each with rule id, "
            "evidence, wall-clock impact category, and an Apple/WWDC/"
            "Tuist/Bazel citation."
        ),
    )
    parser.add_argument(
        "--project-path", required=True, type=pathlib.Path,
        help="Project root containing *.xcodeproj or *.xcworkspace.",
    )
    parser.add_argument("--scheme", default=None, help="Xcode scheme to inspect.")
    parser.add_argument(
        "--configuration", default="Debug",
        help="Build configuration name (free string; e.g. Debug or Distribution).",
    )
    parser.add_argument(
        "--destination",
        default="generic/platform=iOS Simulator",
        help="xcodebuild destination (passed to -showBuildSettings if relevant).",
    )
    parser.add_argument(
        "--platform", default="ios",
        help="Apple platform (v1 enforces 'ios'; v2 adds macOS / watchOS / tvOS / visionOS).",
    )
    parser.add_argument(
        "--measurement-artifact", default=None, type=pathlib.Path,
        help="Path to a measurement.json (ios-build-measure output).",
    )
    parser.add_argument(
        "--output-dir", required=True, type=pathlib.Path,
        help="Where diagnosis.json + logs are written.",
    )
    parser.add_argument(
        "--skip-xcodebuild", action="store_true",
        help=(
            "Skip xcodebuild -showBuildSettings (use when offline / VPN "
            "down). Build-setting findings short-circuit; pbxproj + SPM "
            "rules still run."
        ),
    )
    parser.add_argument(
        "--resolved-settings-json", default=None, type=pathlib.Path,
        help=(
            "Path to a pre-captured `xcodebuild -showBuildSettings -json` "
            "dump (list-of-targets shape OR a flat key/value dict). When "
            "set, the live xcodebuild call is skipped and the dump is "
            "used instead. Useful for offline / pinned-baseline runs."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    artifact = diagnose(
        project_path=args.project_path,
        scheme=args.scheme,
        configuration=args.configuration,
        destination=args.destination,
        platform=args.platform,
        measurement_artifact=args.measurement_artifact,
        output_dir=args.output_dir,
        skip_xcodebuild=args.skip_xcodebuild,
        resolved_settings_json=args.resolved_settings_json,
    )

    out_path = args.output_dir / "diagnosis.json"
    out_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    summary = artifact["summary"]
    print(
        f"[diagnose] wrote {out_path} — "
        f"{summary['total_findings']} findings, "
        f"{summary['total_additional_recommendations']} additional recommendations"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
