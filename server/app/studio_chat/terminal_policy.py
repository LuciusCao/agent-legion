"""Admission policy for ACP ``terminal/create`` in Studio chat (#921).

Three fences, applied before any subprocess is spawned:

* **Environment allowlist** — a terminal child never inherits the server
  process environment (which carries deployment secrets loaded from
  ``.env``). It gets the same minimal base the ACP SDK uses for the agent
  itself plus locale/tempdir basics (the ``shared/code_sandbox.child_env``
  idea), then only those agent overrides that cannot change which program
  runs or inject code ahead of it (``OVERRIDE_ENV_KEYS``).
* **Working directory pinning** — the requested cwd is walked from the
  session root one component at a time with ``O_NOFOLLOW``
  (``fs_safety.open_dir_beneath``); the child ``fchdir``s to that pinned
  descriptor, so no path is re-resolved between the check and the spawn
  and a component swapped for a symlink cannot move the cwd out of root.
* **Permission linkage** — see terminal_grants.py.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import PurePosixPath
from typing import Any

from acp import RequestError

from server.app.fs_safety import PathEscapeError, open_dir_beneath

# acp.transports.DEFAULT_INHERITED_ENV_VARS (what the SDK keeps for the agent
# subprocess) plus TMPDIR; LANG/LC_* are added by prefix below.
BASE_ENV_KEYS = ("HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER", "TMPDIR")

# Agent-supplied env overrides: only plain formatting/locale values that can
# never name a program or code to run. Everything else is dropped — keys
# that steer program resolution or inject code (PATH, LD_*/DYLD_*, BASH_ENV,
# PYTHONPATH, NODE_OPTIONS, ...) and program selectors (PAGER/GIT_PAGER/
# LESS*, EDITOR/VISUAL, GIT_SSH*, *ASKPASS, BROWSER, SHELL, ...). LANG/LC_*
# are accepted by prefix; GIT_TERMINAL_PROMPT is a plain on/off switch.
OVERRIDE_ENV_KEYS = frozenset(
    {"NO_COLOR", "FORCE_COLOR", "TERM", "COLUMNS", "LINES", "TZ", "GIT_TERMINAL_PROMPT"}
)


def terminal_env(overrides: Iterable[Any] | None) -> dict[str, str]:
    """Allowlisted base environment plus allowlisted agent overrides."""
    env: dict[str, str] = {}
    for key in BASE_ENV_KEYS:
        value = os.environ.get(key)
        if value is not None and not value.startswith("()"):
            env[key] = value
    for key, value in os.environ.items():
        if key == "LANG" or key.startswith("LC_"):
            env[key] = value
    for item in overrides or []:
        name = str(item.name)
        if name in OVERRIDE_ENV_KEYS or name == "LANG" or name.startswith("LC_"):
            env[name] = str(item.value)
    return env


def _cwd_parts(requested: str | None, real_root: str) -> list[str]:
    if not requested:
        return []
    path = PurePosixPath(os.path.expanduser(requested))
    if path.is_absolute():
        root = PurePosixPath(real_root)
        if path != root and root not in path.parents:
            raise PathEscapeError("terminal cwd outside the session root")
        path = path.relative_to(root)
    parts = [part for part in path.parts if part != "."]
    if ".." in parts:
        raise PathEscapeError("terminal cwd must not contain '..'")
    return parts


@contextmanager
def pinned_cwd(requested: str | None, root: str) -> Iterator[int]:
    """Yield a directory descriptor for the terminal cwd inside the session
    root; the child must ``fchdir`` to it (never re-resolve the path)."""
    real_root = os.path.realpath(root)
    with ExitStack() as stack:
        try:
            fd = stack.enter_context(open_dir_beneath(real_root, _cwd_parts(requested, real_root)))
        except (PathEscapeError, OSError) as exc:
            # #1136: reason rides in the message too — engines drop `data`.
            reason = "terminal cwd outside the session root or not a plain directory"
            raise RequestError(-32602, f"Invalid params: {reason}", {"reason": reason}) from exc
        yield fd


def enter_pinned_cwd(fd: int) -> None:
    """Child-side ``preexec_fn``: chdir to the pinned descriptor, then drop
    it so the exec'd program does not inherit an extra open directory."""
    os.fchdir(fd)
    os.close(fd)
