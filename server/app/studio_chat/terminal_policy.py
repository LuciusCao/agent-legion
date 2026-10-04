"""Admission policy for ACP ``terminal/create`` in Studio chat (#921).

Three fences, applied before any subprocess is spawned:

* **Environment allowlist** — a terminal child never inherits the server
  process environment (which carries deployment secrets loaded from
  ``.env``). It gets the same minimal base the ACP SDK uses for the agent
  itself plus locale/tempdir basics (the ``shared/code_sandbox.child_env``
  idea), then only those agent overrides that cannot change which program
  runs or inject code ahead of it (``OVERRIDE_ENV_KEYS``).
* **Working directory confinement** — the requested cwd must resolve
  (symlinks followed) inside the session's working directory.
* **Permission linkage** — see terminal_grants.py.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from acp import RequestError

# acp.transports.DEFAULT_INHERITED_ENV_VARS (what the SDK keeps for the agent
# subprocess) plus TMPDIR; LANG/LC_* are added by prefix below.
BASE_ENV_KEYS = ("HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER", "TMPDIR")

# Agent-supplied env overrides that only shape output/interactivity. Keys
# that steer program resolution or inject code before the approved command
# runs (PATH, LD_*/DYLD_*, BASH_ENV, PYTHONPATH, NODE_OPTIONS, ...) are
# dropped; LANG/LC_* are accepted by prefix.
OVERRIDE_ENV_KEYS = frozenset(
    {
        "NO_COLOR",
        "FORCE_COLOR",
        "TERM",
        "COLUMNS",
        "LINES",
        "GIT_TERMINAL_PROMPT",
        "PAGER",
        "GIT_PAGER",
    }
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


def confined_cwd(requested: str | None, root: str) -> str:
    """Resolve the terminal cwd; refuse anything outside the session root."""
    real_root = os.path.realpath(root)
    if not requested:
        return real_root
    resolved = os.path.realpath(os.path.join(real_root, os.path.expanduser(requested)))
    try:
        inside = os.path.commonpath([resolved, real_root]) == real_root
    except ValueError:
        inside = False
    if not inside:
        raise RequestError.invalid_params({"reason": "terminal cwd outside the session root"})
    return resolved
