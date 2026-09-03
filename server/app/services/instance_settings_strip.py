"""Read-time stripping of retired content from the stored ``instance`` document.

Split from ``instance_settings.py`` (#385/#389) when the key-level strip for
the retired ``workflows.enabled`` outgrew the parent's size budget. The
stripping happens at read time, before response validation, so stored
documents from older deployments keep validating without a data migration.
"""

from __future__ import annotations

from typing import Any

# Host-private state blocks riding the stored ``instance`` document: they
# are written by background machinery through InstanceSettingsStore.update
# and must never surface in the admin contract (InstanceSettingsDocument is
# extra=forbid, so a stray block would 500 the GET/PUT round-trip).
_PRIVATE_BLOCKS = ("openclaw", "execution_retention_cursor")

# Retired nested keys (#385/#389): key-level (not block-level) because the
# surrounding ``workflows`` block still carries the active
# ``max_items_per_run``.
_RETIRED_NESTED_KEYS = {"workflows": ("enabled",)}


def strip_retired(stored: dict[str, Any]) -> dict[str, Any]:
    """Remove non-contract content from a stored document copy.

    - ``openclaw``: retired with the openclaw runtime (#75); stored documents
      from older deployments still carry it.
    - ``execution_retention_cursor`` (#354): the retention sweep's persisted
      keyset high-water marks; a wiped cursor only costs the sweep a reset.
    - ``workflows.enabled`` (#385/#389): the retired gray-release switch.
    """
    if not _needs_strip(stored):
        return stored
    stripped = {k: v for k, v in stored.items() if k not in _PRIVATE_BLOCKS}
    for block, keys in _RETIRED_NESTED_KEYS.items():
        nested = stripped.get(block)
        if isinstance(nested, dict) and any(key in nested for key in keys):
            stripped[block] = {k: v for k, v in nested.items() if k not in keys}
    return stripped


def _needs_strip(stored: dict[str, Any]) -> bool:
    if any(block in stored for block in _PRIVATE_BLOCKS):
        return True
    return any(
        key in nested
        for block, keys in _RETIRED_NESTED_KEYS.items()
        if isinstance((nested := stored.get(block)), dict)
        for key in keys
    )
