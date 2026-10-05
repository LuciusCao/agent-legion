"""Shell variables must not be immediately followed by a non-ASCII byte (#985).

macOS ships bash 3.2 as ``/bin/bash``. Under a UTF-8 locale it treats the
lead byte of a multibyte character as a legal identifier character, so
``"$X，"`` expands the (unset) variable ``X\\xef`` instead of ``X``: under
``set -u`` the script aborts with ``unbound variable``; without it the value
silently vanishes and the output is corrupted. Bash 5 is unaffected, which is
why Linux CI never sees it. Rule: in every tracked shell source, a bare
``$NAME`` must not be directly followed by a non-ASCII byte — write
``${NAME}`` instead.

Scope: ``*.sh`` / ``*.bash``, git hook directories, ``Makefile`` / ``*.mk``
(recipe lines are shell; ``$$NAME`` hits as ``$NAME``) and any tracked file
whose shebang names a shell. Only ``$`` the shell would expand are judged
(#1022): comments, single quotes, ``\\$`` escapes, quoted heredoc bodies and
make-level ``$`` references are skipped, while multi-line double-quoted
strings and unquoted heredoc bodies are scanned on every physical line —
lexical state lives in ``shell_lexer``.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

from .shell_lexer import expanding_dollars, makefile_shell_text

__test__ = False

BARE_VAR_BEFORE_NON_ASCII = re.compile(rb"\$[A-Za-z_][A-Za-z0-9_]*[\x80-\xff]")
_SHELL_SUFFIXES = (".sh", ".bash", ".mk")
_HOOK_DIRS = (".githooks/", "scripts/git-hooks/")
_SHEBANG = re.compile(rb"^#![^\n]*\b(?:ba|z|da|k)?sh\b")


def is_shell_source(path: str, head: bytes) -> bool:
    """Whether a tracked file is executed as shell (by name or shebang)."""
    name = path.rsplit("/", 1)[-1]
    if path.endswith(_SHELL_SUFFIXES) or name == "Makefile":
        return True
    if path.startswith(_HOOK_DIRS):
        return True
    return bool(_SHEBANG.match(head))


def _is_makefile(path: str) -> bool:
    return path.endswith(".mk") or path.rsplit("/", 1)[-1] == "Makefile"


def _shell_chunks(path: str, content: bytes) -> list[tuple[int, bytes]]:
    """(first_lineno, shell text) units: a Makefile's shell-bound lines, else the file."""
    return list(makefile_shell_text(content)) if _is_makefile(path) else [(1, content)]


def find_violations(path: str, content: bytes) -> list[str]:
    """Report each expanding bare ``$NAME`` that touches a non-ASCII byte."""
    errors: list[str] = []
    for first_lineno, text in _shell_chunks(path, content):
        for lineno, offset in expanding_dollars(text, first_lineno):
            match = BARE_VAR_BEFORE_NON_ASCII.match(text, offset)
            if match is None:
                continue
            var = match.group()[:-1].decode("ascii")
            errors.append(
                f"{path}:{lineno}: bare {var} directly followed by a non-ASCII "
                f"character; write ${{{var[1:]}}} (bash 3.2 under a UTF-8 locale "
                "merges the multibyte lead byte into the variable name, #985)"
            )
    return errors


def check_paths(root: Path, paths: Iterable[str]) -> list[str]:
    errors: list[str] = []
    for path in sorted(paths):
        try:
            content = (root / path).read_bytes()
        except OSError:
            continue
        if is_shell_source(path, content[:128]):
            errors.extend(find_violations(path, content))
    return errors


def check_shell_var_boundary(root: Path) -> list[str]:
    """Gate entry: scan tracked files via git; skip silently without git
    metadata (synthetic test layouts, non-repo exports)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    paths = [p.decode("utf-8", "surrogateescape") for p in result.stdout.split(b"\0") if p]
    return check_paths(root, paths)
