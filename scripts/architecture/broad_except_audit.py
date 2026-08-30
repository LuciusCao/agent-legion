"""Guard: every broad ``except Exception`` carries a #204 audit note (#298).

The #204 narrowing campaign pinned the semantics — degradation families
swallow, programming errors raise — with an audit comment on each surviving
broad catch explaining WHY the width is correct (example:
``server/app/executors/leases.py``). New broad catches without an audit
note are exactly the regression the campaign closed; this check makes the
note mandatory instead of review-discipline-enforced.

An audit note counts when it appears within 8 lines of the ``except`` arm
or in the first 12 lines of the enclosing function (block audits at the
top of a sweep method cover every catch in it, e.g.
``executors/sweeper.py``).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

__test__ = False

_SCAN_ROOTS = ("server/app", "worker")
# The full marker, not its parts: a bare "#204" elsewhere in the function
# must not bless an unrelated new broad catch (codex review on #308).
_AUDIT_MARKER = "#204 broad-except audit"
_NEAR_LINES = 8
_HEADER_LINES = 12


def _is_broad_catch(exc_type: ast.expr | None) -> bool:
    """Bare ``except:``, ``except Exception``, or a tuple containing it."""
    if exc_type is None:
        return True
    if isinstance(exc_type, ast.Name):
        return exc_type.id == "Exception"
    if isinstance(exc_type, ast.Tuple):
        return any(_is_broad_catch(item) for item in exc_type.elts)
    return False


def _has_audit_note(source_lines: list[str], start: int) -> bool:
    """Audit marker within 8 lines after the except arm, or in the
    enclosing function's first 12 lines (block audits cover the method)."""
    near = "\n".join(source_lines[start : start + _NEAR_LINES])
    if _AUDIT_MARKER in near:
        return True
    # Walk back to the enclosing def: an audit block at the method head
    # governs every catch inside it.
    for j in range(start, max(-1, start - 120), -1):
        if re.match(r"\s*(def |class )", source_lines[j]):
            header = "\n".join(source_lines[j : j + _HEADER_LINES])
            return _AUDIT_MARKER in header
    return False


def find_unaudited_broad_excepts(source: str) -> list[int]:
    """Line numbers (1-based) of ``except Exception`` arms without an audit."""
    tree = ast.parse(source)
    lines = source.split("\n")
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
    """Reject broad catches without #204 audit notes in the scan roots."""
    errors: list[str] = []
    for base in _SCAN_ROOTS:
        directory = root / base
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            try:
                unaudited = find_unaudited_broad_excepts(source)
            except SyntaxError:
                continue  # unparseable: ruff/mypy own that failure
            rel = path.relative_to(root).as_posix()
            for lineno in unaudited:
                errors.append(
                    f"{rel}:{lineno}: broad except without a #204 audit "
                    "note; narrow to the real exception family or add the "
                    "audit comment (see executors/leases.py for the format)"
                )
    return errors
