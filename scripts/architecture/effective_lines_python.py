"""Python token and docstring scanning for effective-line counting."""

from __future__ import annotations

import ast
import io
import tokenize

__test__ = False

_DOCSTRING_OWNERS = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _python_effective_lines(text: str) -> int:
    lines = text.splitlines()
    free_rows: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type != tokenize.COMMENT:
                continue
            row, col = tok.start
            if row - 1 < len(lines) and not lines[row - 1][:col].strip():
                free_rows.add(row)
        free_rows |= _python_docstring_rows(ast.parse(text), lines)
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        # Unparseable file: fall back to raw counting (the stricter metric).
        # Both parsers run inside this try: a file that tokenizes yet fails
        # ast.parse (a plain grammar violation like ``None = 1``) must hit
        # the raw fallback too — counting it comment-free would undercut
        # the very "stricter metric" the fallback promises.
        return len(lines)
    return sum(bool(line.strip()) and row not in free_rows for row, line in enumerate(lines, 1))


def _python_docstring_rows(tree: ast.Module, lines: list[str]) -> set[int]:
    """Rows entirely occupied by a docstring (free per #610).

    A docstring is the first statement of a module/class/function and must
    be a plain string constant — the same population ``ast.get_docstring``
    recognizes. f-strings and later orphan string expressions are code
    (values, not documentation) and stay counted. Only rows the docstring
    occupies alone are freed; a row that also carries code (a one-line
    ``def`` whose body is the docstring) keeps counting, mirroring the
    trailing-comment rule. The tree comes pre-parsed from
    ``_python_effective_lines``: an ast failure there triggers the raw
    fallback, so a parse error can never reach this function.
    """
    rows: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, _DOCSTRING_OWNERS) and node.body):
            continue
        first = node.body[0]
        if not isinstance(first, ast.Expr) or not (
            isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)
        ):
            continue
        start, end = first.lineno, first.end_lineno or first.lineno
        for row in range(start, min(end, len(lines)) + 1):
            # A row keeps counting when code shares it with the docstring:
            # code before the opening quotes (first row) or after the
            # closing quotes (last row, a trailing comment excepted). ast
            # col offsets are UTF-8 BYTE offsets into the source, so the
            # boundary checks slice the line's bytes, never the str (a
            # non-ASCII docstring would otherwise shift every cut and let
            # a code-carrying row pass as free — codex review on #612).
            if row == start and lines[row - 1].encode()[: first.col_offset].strip():
                continue
            if row == end:
                suffix = lines[row - 1].encode()[first.end_col_offset :]
                if suffix.strip() and not suffix.lstrip().startswith(b"#"):
                    continue
            rows.add(row)
    return rows
