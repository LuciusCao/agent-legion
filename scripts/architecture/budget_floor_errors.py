"""Error message rendering for the budget monotonicity check.

Kept separate so ``budget_monotonicity.py`` (144/144 at #231, zero headroom)
stays within its own ceiling while the check grows (#236): the floor
computation lives there, the wording lives here. Callers pass a computed
floor plus the rename origin (old path) when the floor was carried across a
rename; plain raises render without the rename suffix. When the floor came
from the ``AGENT_LEGION_BUDGET_BASE`` anchor the suffix names that base ref,
so a locally-green / CI-red judgement points at its actual source.
"""

from __future__ import annotations

from .budget_anchors import BASE_ANCHOR_OVERRIDE_ENV

__test__ = False


def _anchor_suffix(anchor: str | None) -> str:
    return f" (floor from base anchor {anchor} via {BASE_ANCHOR_OVERRIDE_ENV})" if anchor else ""


def baseline_raise_error(
    path: str, ceiling: int, floor: int, origin: str | None, anchor: str | None
) -> str:
    """Baseline entry rose above the committed floor."""
    suffix = f" (renamed from {origin}; rename does not reset the floor)" if origin else ""
    return (
        f"{path}: ceiling {ceiling} rose above committed ceiling "
        f"{floor}{suffix}{_anchor_suffix(anchor)}; ceilings only ratchet down — "
        "split the file or file a dated architecture.file_budget exemption"
    )


def exemption_raise_error(path: str, ceiling: int, floor: int, anchor: str | None) -> str:
    """Exemption ceiling rose above the committed floor."""
    return (
        f"{path}: exemption ceiling {ceiling} rose above committed "
        f"ceiling {floor}{_anchor_suffix(anchor)}; exemption ceilings only ratchet "
        "down — re-file the exemption or split the file"
    )
