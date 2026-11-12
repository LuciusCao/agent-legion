"""Makefile non-recipe lines for ``shell_var_boundary`` (#1096, #1219 review).

Split from ``shell_sources`` for its file budget. A non-recipe line (rule,
assignment, ``define`` body) is not shell itself, but an assignment value is
pasted into recipes by ``$(VAR)``. Make semantics apply, not shell ones:

- quotes are ordinary characters (the pasting recipe decides the shell
  quoting), so every ``$$NAME`` is judged as expanding (``pasted_value_offsets``);
- an unescaped ``#`` outside a ``$(…)`` / ``${…}`` reference starts a make
  comment that never reaches a recipe (``strip_make_comment``), except inside
  a ``define`` body, where ``#`` is value text.
"""

from __future__ import annotations

import re

__test__ = False

DEFINE = re.compile(rb"^\s*(?:(?:override|export|private)\s+)*define\b")
ENDEF = re.compile(rb"^\s*endef\b")


_CLOSER = {ord("("): ord(")"), ord("{"): ord("}")}


def strip_make_comment(line: bytes) -> bytes:
    """``line`` without its make comment. A ``#`` inside a reference is kept
    (errs towards reporting); ``\\#`` (odd backslash run) is a literal ``#``.
    Reference tracking is a stack of opening-bracket types (#1253): only the
    closer matching the stack top pops it, so a literal mismatched closer in a
    function argument (``$(subst },x,# $$X，)``) does not end the reference."""
    stack: list[int] = []
    i = 0
    while i < len(line):
        byte = line[i]
        if byte == ord("$") and i + 1 < len(line):
            if line[i + 1] in b"({":
                stack.append(line[i + 1])
            i += 2  # ``$$`` / ``$X`` / ``$(``: never a comment start
            continue
        if stack and byte in b"({":
            stack.append(byte)
        elif stack and byte in b")}" and byte == _CLOSER[stack[-1]]:
            stack.pop()
        elif byte == ord("#") and not stack:
            backslashes = len(line[:i]) - len(line[:i].rstrip(b"\\"))
            if backslashes % 2 == 0:
                return line[:i]
        i += 1
    return line


def pasted_value_offsets(text: bytes, lineno: int) -> list[tuple[int, int]]:
    """Every shell ``$`` of a masked non-recipe line except ``$$`` (PID):
    ``MSG = '$$X，'`` used as ``echo "$(MSG)"`` expands ``$X``."""
    found: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        if text[i] == ord("$"):
            if text.startswith(b"$$", i):
                i += 2
                continue
            found.append((lineno, i))
        i += 1
    return found
