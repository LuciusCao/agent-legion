"""Machine-readable contract probing for skill directories (#542).

One shared decision function for "would the velites contract engine find a
contract here?" — consumed by the Host-side engine spawn gate
(``workflows/output_contract_engine``) and the authoring-side validation
(``services/skill_edit_checks``). The semantics mirror the Rust
``Contract::parse`` three-tier resolution in ``velites/src/contract.rs``:

1. skill-root ``contract.yaml`` EXISTS → normative source. Presence alone
   decides the tier: even a broken/unreadable root file means "a contract
   is declared" (velites fails closed on it; the probe must never skip the
   engine for it).
2. no root file, but ``references/output-contract.md`` embeds a
   ```` ```yaml contract ```` block (the first fence whose stripped line
   equals the marker) → deprecated but valid source.
3. neither → nothing declared.

Cross-language drift guard: if the Rust scanner changes its marker or adds
a tier, this probe MUST follow (the fence-variant tests in
``tests/workflows/test_output_validation.py`` and the probe tests in
``tests/services/test_skill_edit_checks.py`` are the tripwire; drift in the
missed-spawn direction would skip the authoritative engine — the only
correctness regression this probe could cause). The line splitting is
deliberately a superset of Rust's ``lines()``: ``splitlines()`` also splits
on \\r/\\v/\\f/U+2028 et al., so a fence line those separators hide from
velites but not from us can only produce an extra (harmless) spawn, never
a missed one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

# Location constants shared with velites/src/contract.rs (#542).
CONTRACT_YAML = "contract.yaml"
CONTRACT_DOC = "references/output-contract.md"
# The fence marker the Rust scanner matches (a line that strips to exactly
# this). Public: the pinned-ref publish advisory (workflows/skill_repo_gate)
# re-implements the tier-2 presence scan against a git tag's tree.
CONTRACT_FENCE = "```yaml contract"
_CONTRACT_FENCE = CONTRACT_FENCE  # historical private alias

ContractProbe = Literal[
    "root_yaml",  # tier 1: skill-root contract.yaml present (normative)
    "embedded_block",  # tier 2: deprecated block in output-contract.md
    "undetermined",  # a read failed: assume declared, velites fails closed
    "none",  # tier 3: no contract anywhere
]


def probe_contract(skill_dir: Path) -> ContractProbe:
    """The three-tier presence probe (never parses, never raises).

    ``undetermined`` reports "declared" on read failures other than
    not-found (unreadable file, non-UTF-8 content): the engine is the
    authority and fails closed there, so the spawn must happen.
    """
    root = skill_dir / CONTRACT_YAML
    try:
        # Existence (not is_file): a directory / symlink-to-directory at the
        # root-contract path is still a DECLARED contract — velites's
        # read_to_string fails closed there (EISDIR etc.), and only NotFound
        # falls through to tier 2. is_file() would wrongly report "none" and
        # skip the engine, bypassing the fail-closed verdict (codex review).
        if root.exists():
            return "root_yaml"
    except OSError:
        # Undeterminable: assume a contract and let velites fail closed.
        return "undetermined"
    try:
        content = (skill_dir / CONTRACT_DOC).read_text(encoding="utf-8")
    except FileNotFoundError:
        return "none"
    except (OSError, UnicodeDecodeError):
        return "undetermined"
    if any(line.strip() == _CONTRACT_FENCE for line in content.splitlines()):
        return "embedded_block"
    return "none"


def has_machine_contract(skill_dir: Path) -> bool:
    """Spawn-gate answer: would the engine find a contract to check?

    True for every tier except ``none`` — including ``undetermined`` (a
    read failure is velites's fail-closed case, not a degrade signal).
    """
    return probe_contract(skill_dir) != "none"
