"""Display-only migration notices for skill machine-contract source tiers."""

from __future__ import annotations

from pathlib import Path

from server.app.skills.contract_probe import probe_contract

_EMBEDDED_WARNING = (
    "contract.yaml is missing; the machine contract still lives in the "
    "deprecated embedded block (migrate it to a root contract.yaml)"
)
_MISSING_WARNING = (
    "contract.yaml is missing; runtime output validation degrades to "
    "existence-only mode (add a root contract.yaml)"
)


def contract_warnings(content_dir: Path) -> list[dict[str, str]]:
    """Warn only when the root is missing; malformed roots use the error channel."""
    tier = probe_contract(content_dir)
    if tier == "embedded_block":
        return [{"path": "contract.yaml", "error": _EMBEDDED_WARNING}]
    if tier in ("none", "undetermined"):
        return [{"path": "contract.yaml", "error": _MISSING_WARNING}]
    return []


def contract_tier_warnings(path: Path) -> tuple[str, ...]:
    """Return display-only contract migration nudges for the editor."""
    tier = probe_contract(path)
    if tier == "embedded_block":
        return (
            "machine contract lives in the deprecated embedded block; "
            "migrate to a root contract.yaml",
        )
    if tier in ("none", "undetermined"):
        return ("no machine contract; runtime output validation is existence-only",)
    return ()
