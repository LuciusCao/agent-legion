"""Minimal stdlib reader for Linux ``/proc`` process records (#682).

Slim container images ship without ``ps`` (procps), so process identity and
parentage checks read ``/proc/<pid>/stat`` and ``/proc/<pid>/cmdline``
directly. ``proc_root`` is injectable so tests can point at a fake tree.
Every reader degrades to ``None`` / empty on races (process exited between
listing and reading) and on permission errors — callers treat "unreadable"
as "not a match", never as an error.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

PROC_ROOT = Path("/proc")


@dataclass(frozen=True)
class ProcStat:
    pid: int
    state: str
    ppid: int
    pgid: int
    sid: int
    starttime: int


def parse_stat(text: str) -> ProcStat | None:
    """Parse one ``/proc/<pid>/stat`` line.

    ``comm`` (field 2) is parenthesised and may itself contain spaces and
    ``)``, so the fixed fields are split after the *last* ``)``.
    """
    head, sep, tail = text.rpartition(")")
    if not sep:
        return None
    pid_text = head.split("(", 1)[0].strip()
    fields = tail.split()
    # fields[0]=state(3) [1]=ppid(4) [2]=pgrp(5) [3]=session(6) ... [19]=starttime(22)
    if not pid_text.isdigit() or len(fields) < 20:
        return None
    try:
        return ProcStat(
            pid=int(pid_text),
            state=fields[0],
            ppid=int(fields[1]),
            pgid=int(fields[2]),
            sid=int(fields[3]),
            starttime=int(fields[19]),
        )
    except ValueError:
        return None


def read_stat(pid: int, proc_root: Path = PROC_ROOT) -> ProcStat | None:
    try:
        return parse_stat(
            (proc_root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        return None


def read_cmdline(pid: int, proc_root: Path = PROC_ROOT) -> str:
    """argv joined by spaces (NUL-separated on disk); empty for zombies/kernel threads."""
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
    except OSError:
        return ""
    return raw.rstrip(b"\0").replace(b"\0", b" ").decode("utf-8", errors="replace")


def iter_pids(proc_root: Path = PROC_ROOT) -> Iterator[int]:
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name.isdigit():
            yield int(entry.name)


def child_pids(parent: int, proc_root: Path = PROC_ROOT) -> list[int]:
    """Direct children of ``parent``.

    Prefers ``/proc/<parent>/task/*/children`` (one small file per thread, no
    full-table scan — matters when tens of thousands of processes are live);
    kernels without CONFIG_PROC_CHILDREN fall back to scanning every stat.
    """
    task_dir = proc_root / str(parent) / "task"
    found: set[int] = set()
    saw_children_file = False
    try:
        tasks = list(task_dir.iterdir())
    except OSError:
        tasks = []
    for task in tasks:
        try:
            text = (task / "children").read_text(encoding="utf-8")
        except OSError:
            continue
        saw_children_file = True
        found.update(int(token) for token in text.split() if token.isdigit())
    if saw_children_file:
        return sorted(found)
    return sorted(
        pid
        for pid in iter_pids(proc_root)
        if (stat := read_stat(pid, proc_root)) is not None and stat.ppid == parent
    )


def pgid_members(proc_root: Path = PROC_ROOT) -> dict[int, list[int]] | None:
    """One full-table snapshot ``pgid -> [pid, ...]``; None without ``/proc``.

    Lets a caller verifying many process groups pay for one ``/proc`` scan
    instead of one per group.
    """
    if not proc_root.is_dir():
        return None
    members: dict[int, list[int]] = {}
    for pid in iter_pids(proc_root):
        if (stat := read_stat(pid, proc_root)) is not None:
            members.setdefault(stat.pgid, []).append(pid)
    return members


@dataclass(frozen=True)
class GroupIdentity:
    """A verified process group, pinned to the exact processes seen at verification.

    ``members`` holds ``(pid, starttime)`` of every group member read live when
    the marker was confirmed; ``(pid, starttime)`` is never reused, so a later
    signal can prove the group is still *that* group, not a recycled pgid.
    ``members is None`` means the ``ps`` fallback (no ``/proc``, no starttime):
    ownership is then re-proven by re-running the marker check.
    """

    pgid: int
    marker: str
    members: frozenset[tuple[int, int]] | None


def group_identity(
    pgid: int,
    marker: str,
    proc_root: Path = PROC_ROOT,
    members: dict[int, list[int]] | None = None,
) -> GroupIdentity | None:
    """Verify that a live process in ``pgid`` has ``marker`` in its argv.

    Reads ``/proc`` when present (slim images have no ``ps``); falls back to
    ``ps`` only where there is no ``/proc`` (macOS dev). ``members`` (from
    ``pgid_members``) only narrows the candidates — each candidate's pgid and
    cmdline are re-read live, so a stale snapshot can miss a group but never
    vouch for a pid that has since left it. Zombies carry an empty cmdline and
    never count as a live marker holder. None = unverifiable.
    """
    if not proc_root.is_dir():
        return GroupIdentity(pgid, marker, None) if _ps_group_has_marker(pgid, marker) else None
    candidates = members.get(pgid, []) if members is not None else iter_pids(proc_root)
    pinned: set[tuple[int, int]] = set()
    verified = False
    for pid in candidates:
        stat = read_stat(pid, proc_root)
        if stat is None or stat.pgid != pgid:
            continue
        pinned.add((pid, stat.starttime))
        verified = verified or marker in read_cmdline(pid, proc_root)
    return GroupIdentity(pgid, marker, frozenset(pinned)) if verified else None


def still_owned(identity: GroupIdentity, proc_root: Path = PROC_ROOT) -> bool:
    """Re-prove right before a signal that ``identity.pgid`` is still the verified group.

    True while any pinned member still exists with the same starttime inside the
    same pgid (a pgid cannot be recycled while any member — zombies included —
    still holds it). Survivors need not carry the marker: once the marker-bearing
    leader died of SIGTERM, its pinned children are what SIGKILL must still reach.
    """
    if identity.members is None:
        return _ps_group_has_marker(identity.pgid, identity.marker)
    return any(
        (stat := read_stat(pid, proc_root)) is not None
        and stat.starttime == starttime
        and stat.pgid == identity.pgid
        for pid, starttime in identity.members
    )


def group_has_marker(
    pgid: int,
    marker: str,
    proc_root: Path = PROC_ROOT,
    members: dict[int, list[int]] | None = None,
) -> bool:
    return group_identity(pgid, marker, proc_root, members) is not None


def _ps_group_has_marker(pgid: int, marker: str) -> bool:
    try:
        out = subprocess.run(
            ["ps", "-axo", "pgid=,args="],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except OSError:
        return False
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit() and int(parts[0]) == pgid and marker in parts[1]:
            return True
    return False
