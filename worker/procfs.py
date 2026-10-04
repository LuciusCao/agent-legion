"""Minimal stdlib reader for Linux ``/proc`` process records (#682).

Slim container images ship without ``ps`` (procps), so process identity and
parentage checks read ``/proc/<pid>/stat`` and ``/proc/<pid>/cmdline``
directly. ``proc_root`` is injectable so tests can point at a fake tree.
Every reader degrades to ``None`` / empty on races (process exited between
listing and reading) and on permission errors — callers treat "unreadable"
as "not a match", never as an error.
"""

from __future__ import annotations

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
