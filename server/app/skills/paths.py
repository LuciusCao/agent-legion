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

import errno
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

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


def ensure_secure_runs_dir(runs_dir: Path) -> Path:
    """Create ``runs_dir`` safely on a shared temp filesystem, or validate it.

    The deterministic path is predictable, so on a world-writable temp dir an
    attacker could otherwise pre-create it (or a symlink at it) and read or
    tamper with the skill snapshots copied inside. Mirrors CPython's
    ``tempfile._mkstemp_inner`` trust rules:

    - ``mkdir(0o700)`` without ``parents=True`` and without ``exist_ok``:
      creation is atomic, so racing a pre-existing entry fails loudly.
    - On ``EEXIST`` the existing entry must be a real directory (lstat — no
      symlink) owned by the current user; mode looser than ``0700`` is
      tightened once via chmod (upgrade path from the earlier permissive
      creation), anything else is a hard error naming the remediation.
    - The parent must already exist (the OS temp dir always does); a pinned
      custom root via ``AGENT_LEGION_SKILLS_RUNS_DIR`` is created by the
      operator with matching guarantees.
    """
    path = Path(runs_dir)
    try:
        os.mkdir(path, mode=0o700)
        return path
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
    # Path exists: validate ownership and that it is not a symlink. lstat is
    # the authority; a dangling or redirected symlink must fail.
    st = os.lstat(path)
    if os.path.islink(path) or not os.path.isdir(path) or st.st_uid != os.geteuid():
        raise OSError(
            f"refusing to use skills runs dir {path}: it exists but is not a "
            "directory owned by the current user (possible pre-created entry "
            "on a shared temp dir). Remove it or pin "
            "AGENT_LEGION_SKILLS_RUNS_DIR to a private path."
        )
    if st.st_mode & 0o777 != 0o700:
        # One-time tighten: an entry created by an older version (or a
        # different umask) that we own. chmod -S keeps us on lstat facts.
        os.chmod(path, 0o700, follow_symlinks=False)
        logger.info("tightened skills runs dir %s to 0700", path)
    return path
