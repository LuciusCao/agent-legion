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
parser: ``$(…)`` opens its own unquoted frame, while backticks and
``${…}`` contents are lexed in the enclosing context, which errs towards
reporting (every one of those contexts expands). ``$$`` is the PID parameter, never a ``$NAME`` prefix.

Fail-closed wrapper and Makefile handling live in ``shell_sources``.
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


class UnterminatedShellContext(Exception):
    """A quote, command substitution or heredoc still open at end of input.

    Usually a lexer blind spot rather than a real script error, and it would
    swallow everything after ``lineno`` — ``dollar_offsets`` fails closed.
    """

    def __init__(self, lineno: int, hits: list[tuple[int, int]] | None = None) -> None:
        super().__init__(lineno)
        self.lineno = lineno
        self.hits = hits or []


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
        if byte == ord("$") and i + 1 < n and content[i + 1] in b"'\"":
            i += 1  # ``$'EOF'`` / ``$"EOF"``: the quoting prefix is not part of WORD
            continue
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
    A body that runs to end of input raises ``UnterminatedShellContext``.
    """
    opened = lineno
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
    raise UnterminatedShellContext(opened, hits)


def expanding_dollars(content: bytes, first_lineno: int = 1) -> Iterator[tuple[int, int]]:
    """Yield (lineno, index) of every ``$`` the shell would expand.

    Contexts form a stack so ``"$(printf '%s' "$x")"`` lexes its inner
    quotes in the command substitution's own unquoted frame; ``depth``
    counts the frame's open parentheses so only the matching ``)`` pops it.
    """
    n = len(content)
    i = 0
    lineno = first_lineno
    # Frames: (kind, open_parens, opened_lineno); kind is plain | subst |
    # single | ansi | double. ``word_start``: an unquoted ``#`` here opens a
    # comment (lexical state, not the previous raw byte: ``foo\ #`` is one word).
    stack: list[tuple[str, int, int]] = [("plain", 0, lineno)]
    pending: list[tuple[bytes, bool, bool]] = []
    word_start = True
    while i < n:
        byte = content[i]
        state, depth, opened = stack[-1]
        unquoted = state in ("plain", "subst")
        if byte == ord("\n"):
            i += 1
            lineno += 1
            word_start = True
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
        if byte in b"\\$" or state == "double":
            # Escapes, expansions and double-quoted text continue the word.
            word_start = False
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
                stack.append(("ansi", 0, lineno))
                i += 2
                continue
            if following == ord("("):
                stack.append(("subst", 0, lineno))
                word_start = True
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
        previous_word_start, word_start = word_start, byte in _WORD_BREAK
        if byte == ord("'"):
            stack.append(("single", 0, lineno))
        elif byte == ord('"'):
            stack.append(("double", 0, lineno))
        elif byte == ord("(") and state == "subst":
            stack[-1] = (state, depth + 1, opened)
        elif byte == ord(")") and state == "subst":
            if depth:
                stack[-1] = (state, depth - 1, opened)
            else:
                stack.pop()
        elif byte == ord("#") and previous_word_start:
            end = content.find(b"\n", i)
            i = n if end < 0 else end
            continue
        elif content.startswith(b"<<<", i):
            i += 3  # here-string: the word that follows is ordinary shell text
            word_start = True
            continue
        elif content.startswith(b"<<", i):
            delimiter, quoted, strip_tabs, i = _read_heredoc_delimiter(content, i + 2)
            # ``(( x << 2 ))`` is an arithmetic shift, not a heredoc.
            if delimiter and not delimiter.isdigit():
                pending.append((delimiter, quoted, strip_tabs))
            continue
        i += 1
    if len(stack) > 1:
        raise UnterminatedShellContext(stack[-1][2])
