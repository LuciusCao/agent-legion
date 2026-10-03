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


def group_has_marker(
    pgid: int,
    marker: str,
    proc_root: Path = PROC_ROOT,
    members: dict[int, list[int]] | None = None,
) -> bool:
    """True when a live process in process group ``pgid`` has ``marker`` in its argv.

    Reads ``/proc`` when present (slim images have no ``ps``); falls back to
    ``ps`` only where there is no ``/proc`` (macOS dev). ``members`` (from
    ``pgid_members``) only narrows the candidates — each candidate's pgid and
    cmdline are re-read live, so a stale snapshot can miss a group but never
    vouch for a pid that has since left it. Zombies carry an empty cmdline and
    never count as a live marker holder.
    """
    if not proc_root.is_dir():
        return _ps_group_has_marker(pgid, marker)
    candidates = members.get(pgid, []) if members is not None else iter_pids(proc_root)
    return any(
        (stat := read_stat(pid, proc_root)) is not None
        and stat.pgid == pgid
        and marker in read_cmdline(pid, proc_root)
        for pid in candidates
    )


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
