"""Error message rendering for the budget monotonicity check.

Kept separate so ``budget_monotonicity.py`` (144/144 at #231, zero headroom)
stays within its own ceiling while the check grows (#236): the floor
computation lives there, the wording lives here. Callers pass a computed
floor plus the rename origin (old path) when the floor was carried across a
rename; plain raises render without the rename suffix.
"""

from __future__ import annotations

__test__ = False


def baseline_raise_error(path: str, ceiling: int, floor: int, origin: str | None) -> str:
    """Baseline entry rose above the committed floor."""
    suffix = f" (renamed from {origin}; rename does not reset the floor)" if origin else ""
    return (
        f"{path}: ceiling {ceiling} rose above committed ceiling "
        f"{floor}{suffix}; ceilings only ratchet down — split the file or "
        "file a dated architecture.file_budget exemption"
    )


def exemption_raise_error(path: str, ceiling: int, floor: int) -> str:
    """Exemption ceiling rose above the committed floor."""
    return (
        f"{path}: exemption ceiling {ceiling} rose above committed "
        f"ceiling {floor}; exemption ceilings only ratchet down — "
        "re-file the exemption or split the file"
    )
