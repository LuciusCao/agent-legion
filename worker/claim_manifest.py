"""Apply Host-resolved live execution settings to an extracted bundle."""

from __future__ import annotations

from typing import Any


def apply_live_manifest(bundled: dict[str, Any], claimed: dict[str, Any]) -> dict[str, Any]:
    live = claimed.get("manifest")
    if not isinstance(live, dict):
        return bundled
    for key in ("pi", "additional_prompt", "command_spec"):
        if key in live:
            bundled[key] = live[key]
    return bundled
