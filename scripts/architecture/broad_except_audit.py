"""Guard: broad catches carry a #204 audit note (#298) — the #204
campaign's "degradation swallow, programming raise" semantics enforced
by machine. The marker must sit inside the arm's own body or the
enclosing function's first 12 lines (block audits cover the method)."""

from __future__ import annotations

import ast
from pathlib import Path

from .broad_except_scan import owner_map, strings_blanked

__test__ = False

_SCAN_ROOTS = ("server/app", "worker")
# The full marker, not its parts: a bare "#204" elsewhere must not bless
# an unrelated new broad catch (codex review on #308).
_AUDIT_MARKER = "#204 broad-except audit"
_HEADER_LINES = 12


def _is_broad_catch(exc_type: ast.expr | None) -> bool:
    """Bare except, Exception/BaseException, or a tuple holding one."""
    if exc_type is None:
        return True
    if isinstance(exc_type, ast.Name):
        return exc_type.id in ("Exception", "BaseException")
    if isinstance(exc_type, ast.Tuple):
        return any(_is_broad_catch(item) for item in exc_type.elts)
    return False


def _has_audit_note(
    source_lines: list[str],
    handler: ast.ExceptHandler,
    owner: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> bool:
    """Marker inside the arm's own body (bounded by the handler's end
    line — a neighbor arm's audit never leaks in), or in the owning
    function's first 12 lines."""
    arm_end = handler.end_lineno or handler.lineno
    arm = "\n".join(source_lines[handler.lineno - 1 : arm_end])
    if _AUDIT_MARKER in arm:
        return True
    if owner is not None:
        header = "\n".join(source_lines[owner.lineno - 1 : owner.lineno - 1 + _HEADER_LINES])
        if _AUDIT_MARKER in header:
            return True
    return False


def find_unaudited_broad_excepts(source: str) -> list[int]:
    """1-based line numbers of broad except arms without an audit note."""
    tree = ast.parse(source)
    # Comment-only view: string literals are blanked so an audit marker
    # must live in a real comment, not inside a log("...") string.
    lines = strings_blanked(source).split("\n")
    owners = owner_map(tree)
    unaudited: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not _is_broad_catch(node.type):
            continue
        if not _has_audit_note(lines, node, owners.get(id(node))):
            unaudited.append(node.lineno)
    return sorted(unaudited)


def check_broad_except_audit(root: Path) -> list[str]:
    """Reject unaudited broad catches in the scan roots."""
    errors: list[str] = []
    for base in _SCAN_ROOTS:
        directory = root / base
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            try:
                unaudited = find_unaudited_broad_excepts(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue  # unparseable: ruff/mypy own that failure
            rel = path.relative_to(root).as_posix()
            errors.extend(
                f"{rel}:{n}: broad except without a #204 audit note; "
                "narrow it or add the audit comment "
                "(see executors/leases.py for the format)"
                for n in unaudited
            )
    return errors
