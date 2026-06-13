"""Architecture invariant registry loader and validator."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml

GATE_PREFIXES: dict[str, tuple[str, ...]] = {
    "quick": ("tests/", "scripts/"),
    "full": ("tests/full/",),
    "ci_extended": ("tests/ci/",),
}

EVIDENCE_KINDS: set[str] = {
    "static",
    "contract",
    "integration",
    "multiprocess",
    "failure_injection",
}

RUNTIME_EVIDENCE_KINDS: set[str] = {
    "integration",
    "multiprocess",
    "failure_injection",
}

RISK_LEVELS: set[str] = {"high", "critical"}

ID_PATTERN: re.Pattern[str] = re.compile(
    r"^(API|DB|EXEC|RECOVERY|BOUNDARY|SECURITY|CONFIG)-[A-Z]+-[0-9]{3}$"
)


@dataclass(frozen=True)
class InvariantEvidence:
    kind: Literal["static", "contract", "integration", "multiprocess", "failure_injection"]
    target: str
    gate: Literal["quick", "full", "ci_extended"]


@dataclass(frozen=True)
class ArchitectureInvariant:
    id: str
    statement: str
    owner: str
    risk: Literal["high", "critical"]
    evidence: tuple[InvariantEvidence, ...]


def _parse_evidence(raw: dict[str, Any]) -> InvariantEvidence:
    """Parse a single evidence entry from the registry YAML."""
    kind = cast(
        Literal["static", "contract", "integration", "multiprocess", "failure_injection"],
        raw.get("kind", ""),
    )
    gate = cast(Literal["quick", "full", "ci_extended"], raw.get("gate", ""))
    return InvariantEvidence(
        kind=kind,
        target=raw.get("target", ""),
        gate=gate,
    )


def _parse_invariant(raw: dict[str, Any]) -> ArchitectureInvariant:
    """Parse a single invariant entry from the registry YAML."""
    invariant_id = raw.get("id", "")
    evidence_raw = raw.get("evidence", [])
    evidence = tuple(_parse_evidence(item) for item in evidence_raw)

    return ArchitectureInvariant(
        id=invariant_id,
        statement=raw.get("statement", ""),
        owner=raw.get("owner", ""),
        risk=cast(Literal["high", "critical"], raw.get("risk", "")),
        evidence=evidence,
    )


def load_registry(path: str | Path) -> tuple[ArchitectureInvariant, ...]:
    """Load architecture invariants from a YAML registry file."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_invariants = data.get("invariants", [])
    return tuple(_parse_invariant(raw) for raw in raw_invariants)


def _target_exists(target: str, base_path: Path) -> tuple[bool, str | None]:
    """Check whether an evidence target resolves to an existing file or test symbol.

    Returns (ok, error_message). For targets containing ``::symbol_name`` the file
    must exist and the symbol must be present in the module's AST. Relative targets
    are resolved against ``base_path``.

    .. note::
        Symbol lookups only inspect top-level function, async function, and class
        definitions. Nested methods, module-level variables, imports, and other
        AST nodes are not considered evidence targets.
    """
    if "::" in target:
        file_part, symbol = target.split("::", 1)
        file_path = base_path / file_part
        if not file_path.exists():
            return False, f"target '{target}' does not exist"
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"))
        except SyntaxError:
            return False, f"target '{target}' contains a syntax error"
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == symbol
            ):
                return True, None
        return False, f"test symbol '{symbol}' not found in '{file_part}'"

    file_path = base_path / target
    if not file_path.exists():
        return False, f"target '{target}' does not exist"
    return True, None


def _is_in_gate(target: str, gate: str) -> bool:
    """Return True when the target lives under one of the gate's allowed prefixes."""
    return any(target.startswith(prefix) for prefix in GATE_PREFIXES[gate])


def validate_registry(
    invariants: tuple[ArchitectureInvariant, ...],
    base_path: str | Path | None = None,
) -> list[str]:
    """Validate a loaded invariant registry and return a list of concise violations."""
    root = Path(base_path) if base_path else Path.cwd()
    errors: list[str] = []
    seen_ids: set[str] = set()

    for inv in invariants:
        if inv.id in seen_ids:
            errors.append(f"duplicate invariant ID: {inv.id}")
            continue
        seen_ids.add(inv.id)

        if not ID_PATTERN.match(inv.id):
            errors.append(f"invalid ID format: {inv.id}")

        if not inv.statement.strip():
            errors.append(f"invariant {inv.id}: statement is empty")

        if not inv.owner.strip():
            errors.append(f"invariant {inv.id}: owner is empty")

        if inv.risk not in RISK_LEVELS:
            errors.append(f"invariant {inv.id}: unsupported risk level '{inv.risk}'")

        if not inv.evidence:
            errors.append(f"invariant {inv.id}: missing evidence")

        for idx, ev in enumerate(inv.evidence, start=1):
            if ev.kind not in EVIDENCE_KINDS:
                errors.append(
                    f"invariant {inv.id}: evidence {idx} unsupported evidence kind '{ev.kind}'"
                )
                continue

            if ev.gate not in GATE_PREFIXES:
                errors.append(f"invariant {inv.id}: evidence {idx} unsupported gate '{ev.gate}'")
                continue

            if not _is_in_gate(ev.target, ev.gate):
                errors.append(
                    f"invariant {inv.id}: evidence {idx} target '{ev.target}' "
                    f"is outside gate '{ev.gate}' allowed prefixes {GATE_PREFIXES[ev.gate]}"
                )

            ok, message = _target_exists(ev.target, root)
            if not ok:
                errors.append(f"invariant {inv.id}: evidence {idx} {message}")

        if inv.risk == "critical":
            has_local_runtime = any(
                ev.gate == "quick" and ev.kind in RUNTIME_EVIDENCE_KINDS for ev in inv.evidence
            )
            has_full_runtime = any(
                ev.gate == "full" and ev.kind in RUNTIME_EVIDENCE_KINDS for ev in inv.evidence
            )

            if not has_local_runtime:
                errors.append(
                    f"invariant {inv.id}: critical invariants require at least one "
                    "local (quick) runtime evidence target"
                )
            if not has_full_runtime:
                errors.append(
                    f"invariant {inv.id}: critical invariants require at least one "
                    "full runtime evidence target"
                )

    return errors
