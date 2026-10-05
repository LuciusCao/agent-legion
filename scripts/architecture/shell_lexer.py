"""Minimal shell / Makefile lexer for the bare-variable boundary guard (#1022).

``shell_var_boundary`` must only judge a ``$NAME`` that the shell would
actually expand. The per-line ``lstrip().startswith('#')`` heuristic it used
before (#985) both over- and under-reported (#991 codex R1):

- ``'$X中'`` (single quotes), ``\\$X中`` (escaped dollar) and a quoted
  heredoc body never expand, yet were flagged;
- a physical line starting with ``#`` inside a multi-line double-quoted
  string or an unquoted heredoc body DOES expand, yet was skipped.

This module tracks just enough lexical state to tell those apart — unquoted
/ single-quoted / ``$'…'`` / double-quoted context, backslash escapes,
word-start ``#`` comments and heredocs (quoted delimiter → literal body,
unquoted → expanding body with ``\\$`` escapes). It is deliberately not a
parser: command substitution, backticks and ``${…}`` contents are lexed in
the enclosing context, which errs towards reporting (every one of those
contexts expands). ``$$`` is the PID parameter, never a ``$NAME`` prefix.

Makefiles are lexed by make semantics: recipe lines (tab-prefixed,
backslash continuations joined) reach a shell; ``$$`` there becomes ``$``
and every other make-level ``$`` reference is expanded by make — not by
bash — so it is masked out. Make comment lines are skipped. Other
non-recipe lines (rules, assignments) are not shell themselves, but an
assignment value is pasted into recipes by ``$(VAR)``, so a ``$$NAME`` there
still ends up as a shell ``$NAME``: those lines get the same masking (a
single make ``$`` never reaches bash, so only ``$$`` can be reported).
"""

from __future__ import annotations

from collections.abc import Iterator

__test__ = False

# Bytes after which an unquoted ``#`` starts a comment (word start).
_WORD_BREAK = frozenset(b" \t\n;&|()<>")
# Bytes that end an unquoted heredoc delimiter word.
_DELIM_END = frozenset(b" \t\n;&|()<>")
# Bytes a backslash escapes inside an unquoted heredoc body.
_HEREDOC_ESCAPABLE = frozenset(b"$`\\\n")
_MAKE_MASK = b"_"


def _read_heredoc_delimiter(content: bytes, i: int) -> tuple[bytes, bool, bool, int]:
    """Parse ``<<[-] WORD`` starting right after ``<<``.

    Returns (delimiter, quoted, strip_tabs, next_index). Any quote or
    backslash in WORD makes the body literal (bash semantics).
    """
    n = len(content)
    strip_tabs = i < n and content[i] == ord("-")
    if strip_tabs:
        i += 1
    while i < n and content[i] in b" \t":
        i += 1
    word = bytearray()
    quoted = False
    while i < n and content[i] not in _DELIM_END:
        byte = content[i]
        if byte in b"'\"":
            quoted = True
            end = content.find(bytes([byte]), i + 1)
            end = n if end < 0 else end
            word += content[i + 1 : end]
            i = end + 1
            continue
        if byte == ord("\\"):
            quoted = True
            if i + 1 < n:
                word.append(content[i + 1])
            i += 2
            continue
        word.append(byte)
        i += 1
    return bytes(word), quoted, strip_tabs, i


def _heredoc_body(
    content: bytes, i: int, lineno: int, heredoc: tuple[bytes, bool, bool]
) -> tuple[list[tuple[int, int]], int, int]:
    """Consume one heredoc body starting at ``i`` (beginning of a line).

    Returns (expanding ``$`` offsets as (lineno, index), next_index, lineno).
    """
    delimiter, quoted, strip_tabs = heredoc
    n = len(content)
    hits: list[tuple[int, int]] = []
    while i < n:
        end = content.find(b"\n", i)
        end = n if end < 0 else end
        line = content[i:end]
        if (line.lstrip(b"\t") if strip_tabs else line) == delimiter:
            return hits, min(end + 1, n), lineno + 1
        if not quoted:
            j = i
            while j < end:
                if content[j] == ord("\\") and j + 1 < n and content[j + 1] in _HEREDOC_ESCAPABLE:
                    j += 2
                    continue
                if content[j] == ord("$"):
                    if j + 1 < n and content[j + 1] == ord("$"):
                        j += 2
                        continue
                    hits.append((lineno, j))
                j += 1
        i = end + 1
        lineno += 1
    return hits, n, lineno


def expanding_dollars(content: bytes, first_lineno: int = 1) -> Iterator[tuple[int, int]]:
    """Yield (lineno, index) of every ``$`` the shell would expand.

    Contexts form a stack so ``"$(printf '%s' "$x")"`` lexes its inner
    quotes in the command substitution's own unquoted frame; ``depth``
    counts the frame's open parentheses so only the matching ``)`` pops it.
    """
    n = len(content)
    i = 0
    lineno = first_lineno
    # Frames: (kind, open_parens); kind is plain | subst | single | ansi | double.
    stack: list[tuple[str, int]] = [("plain", 0)]
    pending: list[tuple[bytes, bool, bool]] = []
    while i < n:
        byte = content[i]
        state, depth = stack[-1]
        unquoted = state in ("plain", "subst")
        if byte == ord("\n"):
            i += 1
            lineno += 1
            if unquoted and pending:
                for heredoc in pending:
                    hits, i, lineno = _heredoc_body(content, i, lineno, heredoc)
                    yield from hits
                pending = []
            continue
        if state in ("single", "ansi"):
            if state == "ansi" and byte == ord("\\"):
                if i + 1 < n and content[i + 1] == ord("\n"):
                    lineno += 1
                i += 2
                continue
            if byte == ord("'"):
                stack.pop()
            i += 1
            continue
        if byte == ord("\\"):
            # Escaped byte (incl. ``\\$`` and line continuation) is literal;
            # a continuation newline still advances the line counter.
            if i + 1 < n and content[i + 1] == ord("\n"):
                lineno += 1
            i += 2
            continue
        if byte == ord("$"):
            following = content[i + 1] if i + 1 < n else None
            if following == ord("$"):
                i += 2
                continue
            if unquoted and following == ord("'"):
                stack.append(("ansi", 0))
                i += 2
                continue
            if following == ord("("):
                stack.append(("subst", 0))
                i += 2
                continue
            yield lineno, i
            i += 1
            continue
        if state == "double":
            if byte == ord('"'):
                stack.pop()
            i += 1
            continue
        # unquoted context (top level or inside a command substitution)
        if byte == ord("'"):
            stack.append(("single", 0))
        elif byte == ord('"'):
            stack.append(("double", 0))
        elif byte == ord("(") and state == "subst":
            stack[-1] = (state, depth + 1)
        elif byte == ord(")") and state == "subst":
            if depth:
                stack[-1] = (state, depth - 1)
            else:
                stack.pop()
        elif byte == ord("#") and (i == 0 or content[i - 1] in _WORD_BREAK):
            end = content.find(b"\n", i)
            i = n if end < 0 else end
            continue
        elif content.startswith(b"<<<", i):
            i += 3  # here-string: the word that follows is ordinary shell text
            continue
        elif content.startswith(b"<<", i):
            delimiter, quoted, strip_tabs, i = _read_heredoc_delimiter(content, i + 2)
            # ``(( x << 2 ))`` is an arithmetic shift, not a heredoc.
            if delimiter and not delimiter.isdigit():
                pending.append((delimiter, quoted, strip_tabs))
            continue
        i += 1


def _mask_make_references(line: bytes) -> bytes:
    """Recipe text as the shell receives it: ``$$`` → ``$``, other make
    ``$`` references masked (make, not bash, expands them)."""
    out = bytearray()
    i = 0
    while i < len(line):
        if line[i] == ord("$"):
            if line.startswith(b"$$", i):
                out += b"$"
            else:
                out += _MAKE_MASK
            i += 2 if i + 1 < len(line) else 1
            continue
        out.append(line[i])
        i += 1
    return bytes(out)


def makefile_shell_text(content: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield (first_lineno, shell_text) per logical recipe line, plus each
    non-comment non-recipe line (its ``$$`` may reach a recipe via ``$(VAR)``)."""
    lines = content.split(b"\n")
    index = 0
    while index < len(lines):
        if not lines[index].startswith(b"\t"):
            if not lines[index].lstrip().startswith(b"#"):
                yield index + 1, _mask_make_references(lines[index])
            index += 1
            continue
        first = index
        logical = [lines[index][1:]]
        while logical[-1].endswith(b"\\") and index + 1 < len(lines):
            index += 1
            nxt = lines[index]
            logical.append(nxt[1:] if nxt.startswith(b"\t") else nxt)
        yield first + 1, _mask_make_references(b"\n".join(logical))
        index += 1
