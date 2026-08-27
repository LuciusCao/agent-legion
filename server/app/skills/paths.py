"""Deterministic scratch locations for the skill runtime.

The runs dir holds per-execution skill snapshots (deep copies handed to
dispatch and output validation) and the cross-process cache lock files. It
is host-side scratch with a seconds-scale lifetime: a process killed between
``copytree`` and its ``finally`` cleanup leaks a snapshot, so the location
must be a temp dir the OS eventually reclaims — never a sibling of the
shared skills base dir under ``~/.agents``, where leaked snapshots pollute
the agent skills namespace.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

RUNS_DIR_PREFIX = "agent-legion-skills.runs"


def default_skills_runs_dir() -> Path:
    """Deterministic per-user temp dir for execution snapshots and cache locks.

    Deliberately not ``tempfile.mkdtemp``: every process sharing one skill
    cache must resolve the same path, or the FileLock domain splits and git
    operations on the cache race. The uid suffix keeps a shared ``/tmp`` safe
    from cross-user pre-creation (pip/uv use the same convention); the macOS
    ``TMPDIR`` is already per-user, making the suffix redundant but harmless
    there. Deployments where the temp dir is not stable across processes
    (e.g. systemd ``PrivateTmp``) must pin ``AGENT_LEGION_SKILLS_RUNS_DIR``
    to a shared path.
    """
    suffix = f"-{os.getuid()}" if hasattr(os, "getuid") else ""
    return Path(tempfile.gettempdir()) / f"{RUNS_DIR_PREFIX}{suffix}"
