"""Caller allowlist ratchet for the published Agent catalog (#932, #440 P1).

Agent node execution profiles resolve through one facade
(``server/app/services/agent_node_profile.py`` + its loader module
``agent_node_profile_catalog.py``). The published-catalog readers named in
``config/architecture/agent-definition-catalog-callers.json`` (``symbols``)
may only be referenced from the baselined ``files``: a new direct caller in
production code (``server/``, ``worker/``, ``scripts/``) is an error, and a
baselined file that no longer references any symbol must be dropped from
the baseline (the allowlist only shrinks). Tests are out of scope.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path, PurePosixPath

BASELINE_RELATIVE_PATH = "config/architecture/agent-definition-catalog-callers.json"
SCANNED_GLOBS = ("server/**/*.py", "worker/**/*.py", "scripts/**/*.py")


class _BaselineError(ValueError):
    """Internal configuration error captured by the check."""


def load_catalog_caller_baseline(path: Path) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(symbols, files)``; require exactly version 1 and normalized entries."""
    if not path.is_file():
        raise _BaselineError(f"Baseline file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _BaselineError(f"Malformed JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"version", "symbols", "files"}:
        raise _BaselineError(
            "Baseline root must be a mapping with exactly {version, symbols, files}"
        )
    if raw.get("version") != 1:
        raise _BaselineError(f"Unsupported baseline version: {raw.get('version')!r}")
    symbols = raw.get("symbols")
    files = raw.get("files")
    if not isinstance(symbols, list) or not all(isinstance(s, str) and s for s in symbols):
        raise _BaselineError("symbols must be a list of non-empty strings")
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        raise _BaselineError("files must be a list of strings")
    normalized = [str(PurePosixPath(entry)) for entry in files]
    if len(set(normalized)) != len(normalized):
        raise _BaselineError("duplicate baseline file entry")
    return frozenset(symbols), frozenset(normalized)


def _referenced_symbols(tree: ast.AST, symbols: frozenset[str]) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in symbols:
            hits.append((node.id, node.lineno))
        elif isinstance(node, ast.Attribute) and node.attr in symbols:
            hits.append((node.attr, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            hits.extend((alias.name, node.lineno) for alias in node.names if alias.name in symbols)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in symbols:
            hits.append((node.name, node.lineno))
    return hits


def check_agent_definition_callers(root: Path) -> list[str]:
    """Reject published-catalog references outside the baselined facade files."""
    try:
        symbols, allowed = load_catalog_caller_baseline(root / BASELINE_RELATIVE_PATH)
    except _BaselineError as exc:
        return [f"agent definition caller baseline: {exc}"]
    errors: list[str] = []
    referencing: set[str] = set()
    paths = sorted({path for pattern in SCANNED_GLOBS for path in root.glob(pattern)})
    for path in paths:
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except SyntaxError:
            continue  # reported by the other architecture checks
        hits = _referenced_symbols(tree, symbols)
        if not hits:
            continue
        referencing.add(relative)
        if relative in allowed:
            continue
        for symbol, lineno in hits:
            errors.append(
                f"{relative}:{lineno}: {symbol} reads the published Agent catalog directly; "
                "resolve agent node profiles via server/app/services/agent_node_profile "
                "(+ agent_node_profile_catalog loaders) instead (#932)"
            )
    for stale in sorted(allowed - referencing):
        errors.append(
            f"{BASELINE_RELATIVE_PATH}: stale entry {stale} no longer references the "
            "published Agent catalog; remove it (the allowlist only shrinks)"
        )
    return errors
