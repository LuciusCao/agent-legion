"""Effective (code-only) line counting for per-file budget ceilings.

Blank lines and comment-only lines are excluded so that fitting a budget
never rewards compressing comments or deleting vertical whitespace — and,
since #610, the same courtesy covers Python docstrings: the first string
statement of a module/class/function is documentation, not code, so a
budget must not reward deleting it (the #293 rationale, closed to its
logical end; every other governed language already counts its doc
comments free). Lines that mix docstring with code still count, the same
discipline as trailing comments. Absolute size limits (production/test
max_lines) keep using raw line counts. A file that fails either parser
(tokenize or ast) falls back to raw counting — the stricter metric.
"""

from __future__ import annotations

from pathlib import Path

from .effective_lines_python import _python_effective_lines

__test__ = False

_C_LIKE_SUFFIXES = (".ts", ".tsx", ".css", ".rs", ".js")


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
