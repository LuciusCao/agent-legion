"""Shell-bound text extraction and the fail-closed scan for ``shell_var_boundary``.

Split from ``shell_lexer`` for its file budget (#1022, #1060 review).

Makefiles are lexed by make semantics: recipe lines (tab-prefixed,
backslash continuations joined) reach a shell; ``$$`` there becomes ``$``
and every other make-level ``$`` reference is expanded by make — not by
bash — so it is masked out. Make comment lines are skipped. Other
non-recipe lines (rules, assignments) are not shell themselves, but an
assignment value is pasted into recipes by ``$(VAR)``, so a ``$$NAME`` there
still ends up as a shell ``$NAME``: those lines get the same masking (a
single make ``$`` never reaches bash, so only ``$$`` can be reported). Their
quotes are plain make text — the pasting recipe's quoting is unknown here — so
every ``$$NAME`` on them is judged as expanding, quoted or not (#1096).
"""

from __future__ import annotations

from collections.abc import Iterator

from .shell_lexer import UnterminatedShellContext, expanding_dollars

__test__ = False

_MAKE_MASK = b"_"


def dollar_offsets(content: bytes, first_lineno: int = 1) -> list[tuple[int, int]]:
    """``expanding_dollars`` that fails closed (#1060 review): when a context
    is still open at end of input, every ``$`` on the lines from where it
    opened onward is reported as expanding, except on ``#``-leading lines
    (the pre-#1022 per-line rule) — a lexer blind spot must never turn the
    rest of the file into an unchecked literal."""
    found: list[tuple[int, int]] = []
    try:
        for hit in expanding_dollars(content, first_lineno):
            found.append(hit)
    except UnterminatedShellContext as exc:
        found.extend(exc.hits)
        offset = 0
        for lineno, line in enumerate(content.split(b"\n"), start=first_lineno):
            if lineno >= exc.lineno and not line.lstrip().startswith(b"#"):
                found.extend((lineno, offset + j) for j, b in enumerate(line) if b == ord("$"))
            offset += len(line) + 1
    return sorted(set(found))


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


def _pasted_value_offsets(text: bytes, lineno: int) -> list[tuple[int, int]]:
    """Every shell ``$`` of a masked non-recipe line except ``$$`` (PID).

    Quotes in a make assignment are ordinary characters — the recipe that
    pastes ``$(VAR)`` decides the shell quoting — so ``MSG = '$$X，'`` used
    as ``echo "$(MSG)"`` expands ``$X``: judge conservatively (#1096)."""
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


def makefile_shell_units(content: bytes) -> Iterator[tuple[bytes, list[tuple[int, int]]]]:
    """Yield (shell_text, expanding ``$`` offsets) per logical recipe line
    (shell-lexed), plus each non-comment non-recipe line (its ``$$`` may reach
    a recipe via ``$(VAR)``; judged without shell quoting)."""
    for lineno, text, recipe in _makefile_shell_text(content):
        yield (
            text,
            (dollar_offsets(text, lineno) if recipe else _pasted_value_offsets(text, lineno)),
        )


def _makefile_shell_text(content: bytes) -> Iterator[tuple[int, bytes, bool]]:
    """Yield (first_lineno, shell_text, is_recipe) per logical recipe line and
    per non-comment non-recipe line."""
    lines = content.split(b"\n")
    index = 0
    while index < len(lines):
        if not lines[index].startswith(b"\t"):
            if not lines[index].lstrip().startswith(b"#"):
                yield index + 1, _mask_make_references(lines[index]), False
            index += 1
            continue
        first = index
        logical = [lines[index][1:]]
        while logical[-1].endswith(b"\\") and index + 1 < len(lines):
            index += 1
            nxt = lines[index]
            logical.append(nxt[1:] if nxt.startswith(b"\t") else nxt)
        yield first + 1, _mask_make_references(b"\n".join(logical)), True
        index += 1
