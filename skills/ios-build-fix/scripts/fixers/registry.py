"""Rule_id -> (preview, apply) dispatch for ios-build-fix.

The orchestrator in ``scripts/fix.py`` looks up a fixer by rule_id and
calls preview() (for the approval gate) followed by apply(). A rule_id
without a registered fixer is rejected with a clear error rather than
silently no-op'd.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Callable

from . import AppliedFix, FixContext
from . import asset_catalog, build_setting, script_phase, spm_graph


PreviewFn = Callable[[list[tuple[int, dict[str, Any]]], FixContext], str]
ApplyFn = Callable[[list[tuple[int, dict[str, Any]]], FixContext], AppliedFix]


@dataclasses.dataclass(frozen=True)
class FixerSpec:
    """A fixer for one rule_id.

    ``auto_apply`` distinguishes auto-applicable v1 fixes (F1, F3, F4,
    F9 — return a real AppliedFix that mutates the tree) from
    informational-only stubs (F5, F6, F7, F8 — return a no-op AppliedFix
    + a manual recipe). The orchestrator refuses an informational rule
    unless ``--allow-manual`` is passed.
    """

    rule_id: str
    family: str
    preview: PreviewFn
    apply: ApplyFn
    auto_apply: bool


def build_registry() -> dict[str, FixerSpec]:
    return {
        spec.rule_id: spec
        for spec in (
            # Auto-applicable v1 surface.
            FixerSpec(
                rule_id="script-phase/random-sleep",
                family="script-phase",
                preview=script_phase.preview_random_sleep,
                apply=script_phase.apply_random_sleep,
                auto_apply=True,
            ),
            FixerSpec(
                rule_id="script-phase/missing-output-declarations",
                family="script-phase",
                preview=script_phase.preview_missing_output_declarations,
                apply=script_phase.apply_missing_output_declarations,
                auto_apply=True,
            ),
            FixerSpec(
                rule_id="build-setting/compilation-cache-disabled",
                family="build-setting",
                preview=build_setting.preview_compilation_cache_disabled,
                apply=build_setting.apply_compilation_cache_disabled,
                auto_apply=True,
            ),
            FixerSpec(
                rule_id="build-setting/eager-linking-disabled",
                family="build-setting",
                preview=build_setting.preview_eager_linking_disabled,
                apply=build_setting.apply_eager_linking_disabled,
                auto_apply=True,
            ),
            # Informational stubs (v1 emits a recipe; auto-apply is no-op).
            FixerSpec(
                rule_id="asset-catalog/incremental-recompile",
                family="asset-catalog",
                preview=asset_catalog.preview_incremental_recompile,
                apply=asset_catalog.apply_incremental_recompile,
                auto_apply=False,
            ),
            FixerSpec(
                rule_id="spm/swift-syntax-not-prebuilt",
                family="spm",
                preview=spm_graph.preview_swift_syntax_not_prebuilt,
                apply=spm_graph.apply_swift_syntax_not_prebuilt,
                auto_apply=False,
            ),
            FixerSpec(
                rule_id="spm/oversized-module",
                family="spm",
                preview=spm_graph.preview_oversized_module,
                apply=spm_graph.apply_oversized_module,
                auto_apply=False,
            ),
        )
    }


class UnregisteredFixer(LookupError):
    """Raised by the orchestrator when no fixer is registered for a rule_id."""


def resolve(rule_id: str) -> FixerSpec:
    registry = build_registry()
    spec = registry.get(rule_id)
    if spec is None:
        raise UnregisteredFixer(
            f"No fixer registered for rule_id={rule_id!r}. "
            f"Registered: {sorted(registry)}"
        )
    return spec
