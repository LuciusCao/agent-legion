"""Guard: broad catches carry a #204 audit note (#298) — the #204
campaign's "degradation swallow, programming raise" semantics enforced
by machine. The marker must sit within 8 lines of the arm or the
method head's first 12 lines (block audits cover the method)."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

__test__ = False

_SCAN_ROOTS = ("server/app", "worker")
# The full marker, not its parts: a bare "#204" elsewhere must not bless
# an unrelated new broad catch (codex review on #308).
_AUDIT_MARKER = "#204 broad-except audit"
_NEAR_LINES = 8
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


def _has_audit_note(source_lines: list[str], start: int) -> bool:
    """Marker within 8 lines of the arm, or the method head's first 12."""
    near = "\n".join(source_lines[start : start + _NEAR_LINES])
    if _AUDIT_MARKER in near:
        return True
    # Walk back to the enclosing def: an audit block at the method head
    # governs every catch inside it.
    for j in range(start, max(-1, start - 120), -1):
        if re.match(r"\s*(async\s+def |def |class )", source_lines[j]):
            header = "\n".join(source_lines[j : j + _HEADER_LINES])
            return _AUDIT_MARKER in header
    return False


def find_unaudited_broad_excepts(source: str) -> list[int]:
    """1-based line numbers of broad except arms without an audit note."""
    tree = ast.parse(source)
    # Comment-only view: string literals are blanked so an audit marker
    # must live in a real comment, not inside a log("...") string.
    lines = _strings_blanked(source).split("\n")
    unaudited: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not _is_broad_catch(node.type):
            continue
        if not _has_audit_note(lines, node.lineno - 1):
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


def _strings_blanked(source: str) -> str:
    """Blank string-literal lines: the marker must live in a comment, not
    a log("...") string (subagent review on #308)."""
    rows: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.STRING:
                rows.update(range(tok.start[0], tok.end[0] + 1))
    except (tokenize.TokenError, SyntaxError):
        pass
    return "\n".join("" if i in rows else line for i, line in enumerate(source.split("\n"), 1))
