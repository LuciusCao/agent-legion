"""Effective (code-only) line counting for per-file budget ceilings.

Blank lines and comment-only lines are excluded so that fitting a budget
never rewards compressing comments or deleting vertical whitespace — and,
since #610, the same courtesy covers Python docstrings: the first string
statement of a module/class/function is documentation, not code, so a
budget must not reward deleting it (the #293 rationale, closed to its
logical end; every other governed language already counts its doc
comments free). Lines that mix docstring with code still count, the same
discipline as trailing comments. Absolute size limits (production/test
max_lines) keep using raw line counts.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

__test__ = False

_C_LIKE_SUFFIXES = (".ts", ".tsx", ".css", ".rs", ".js")

_DOCSTRING_OWNERS = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def count_effective_lines(path: Path) -> int:
    """Count lines that carry code, excluding blank and comment-only lines."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        return _python_effective_lines(text)
    if path.suffix in _C_LIKE_SUFFIXES:
        # Rust is a subset of this grammar: same // and /* */ comments, and
        # raw strings r"…" never span lines without the line itself carrying
        # code, so the C-like scanner is exact enough for budget counting
        # (#202; overcounting only happens for a line that is nothing but a
        # multi-line raw string continuation, which counts as code — the
        # stricter metric).
        return _c_like_effective_lines(text)
    if path.suffix == ".sql":
        # ANSI line comments only: block comments in postgres_schema.sql
        # are rare and inline; overcounting is the stricter metric.
        return sum(not line.lstrip().startswith("--") for line in text.splitlines() if line.strip())
    return len(text.splitlines())


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
        free_rows |= _python_docstring_rows(text, lines)
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        # Unparseable file: fall back to raw counting (the stricter metric).
        return len(lines)
    return sum(bool(line.strip()) and row not in free_rows for row, line in enumerate(lines, 1))


def _python_docstring_rows(text: str, lines: list[str]) -> set[int]:
    """Rows entirely occupied by a docstring (free per #610).

    A docstring is the first statement of a module/class/function and must
    be a plain string constant — the same population ``ast.get_docstring``
    recognizes. f-strings and later orphan string expressions are code
    (values, not documentation) and stay counted. Only rows the docstring
    occupies alone are freed; a row that also carries code (a one-line
    ``def`` whose body is the docstring) keeps counting, mirroring the
    trailing-comment rule.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # The caller's tokenize pass already fell back to raw counting.
        return set()
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
            # closing quotes (last row, a trailing comment excepted).
            if row == start and lines[row - 1][: first.col_offset].strip():
                continue
            if row == end:
                suffix = lines[row - 1][first.end_col_offset :]
                if suffix.strip() and not suffix.lstrip().startswith("#"):
                    continue
            rows.add(row)
    return rows


def _c_like_effective_lines(text: str) -> int:
    effective = 0
    in_block_comment = False
    for line in text.splitlines():
        has_code = False
        i = 0
        n = len(line)
        while i < n:
            if in_block_comment:
                end = line.find("*/", i)
                if end == -1:
                    break
                in_block_comment = False
                i = end + 2
                continue
            ch = line[i]
            if ch.isspace():
                i += 1
                continue
            if line.startswith("//", i):
                break
            if line.startswith("/*", i):
                in_block_comment = True
                i += 2
                continue
            if ch in "'\"`":
                i = _skip_quoted(line, i)
                has_code = True
                continue
            has_code = True
            i += 1
        if has_code:
            effective += 1
    return effective


def _skip_quoted(line: str, start: int) -> int:
    quote = line[start]
    i = start + 1
    n = len(line)
    while i < n and line[i] != quote:
        i += 2 if line[i] == "\\" else 1
    return i + 1
