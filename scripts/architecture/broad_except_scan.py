"""AST helpers for the broad-except audit guard (#298).

Split from ``broad_except_audit.py`` for the file-size budget: handler
ownership (AST parent walk, no line windows) and the comment-only source
view (string literals blanked) live here.
"""

from __future__ import annotations

import ast
import io
import tokenize

__test__ = False


def owner_map(tree: ast.AST) -> dict[int, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Map each ExceptHandler to its nearest enclosing function.

    ast has no parent links; one walk records ownership — bounded by the
    tree, not a line window, so a long method keeps its head audit no
    matter how far the catch sits from the def (codex review #308).
    """
    owners: dict[int, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def visit(node: ast.AST, current: ast.FunctionDef | ast.AsyncFunctionDef | None) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            current = node
        if isinstance(node, ast.ExceptHandler) and current is not None:
            owners[id(node)] = current
        for child in ast.iter_child_nodes(node):
            visit(child, current)

    visit(tree, None)
    return owners


def strings_blanked(source: str) -> str:
    """Blank string-literal lines: the audit marker must live in a
    comment, not a log("...") string (subagent review on #308)."""
    rows: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.STRING:
                rows.update(range(tok.start[0], tok.end[0] + 1))
    except (tokenize.TokenError, SyntaxError):
        pass
    return "\n".join("" if i in rows else line for i, line in enumerate(source.split("\n"), 1))
