"""Effective (code-only) line counting for per-file budget ceilings.

Blank lines and comment-only lines are excluded so that fitting a budget
never rewards compressing comments or deleting vertical whitespace. Lines
that mix code with a trailing comment still count. Absolute size limits
(production/test max_lines) keep using raw line counts.
"""

from __future__ import annotations

import io
import tokenize
from pathlib import Path

__test__ = False

_C_LIKE_SUFFIXES = (".ts", ".tsx", ".css")


def count_effective_lines(path: Path) -> int:
    """Count lines that carry code, excluding blank and comment-only lines."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        return _python_effective_lines(text)
    if path.suffix in _C_LIKE_SUFFIXES:
        return _c_like_effective_lines(text)
    return len(text.splitlines())


def _python_effective_lines(text: str) -> int:
    lines = text.splitlines()
    comment_only_rows: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type != tokenize.COMMENT:
                continue
            row, col = tok.start
            if row - 1 < len(lines) and not lines[row - 1][:col].strip():
                comment_only_rows.add(row)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Unparseable file: fall back to raw counting (the stricter metric).
        return len(lines)
    return sum(
        1
        for row, line in enumerate(lines, start=1)
        if line.strip() and row not in comment_only_rows
    )


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
