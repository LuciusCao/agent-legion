"""Process-group identity checks for the orphan reaper (#682).

Verifies a recorded pgid still belongs to an execution (argv marker, read from
``/proc`` — slim images have no ``ps``; ``ps`` remains the fallback where
``/proc`` is absent), pins the group to ``(pid, starttime)`` member identities,
and re-proves ownership right before every signal so a recycled pgid is never
signalled (codex P1 rounds on #895).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from worker import procfs


def pgid_members(proc_root: Path = procfs.PROC_ROOT) -> dict[int, list[int]] | None:
    """One full-table snapshot ``pgid -> [pid, ...]``; None without ``/proc``.

    Lets a caller verifying many process groups pay for one ``/proc`` scan
    instead of one per group.
    """
    if not proc_root.is_dir():
        return None
    members: dict[int, list[int]] = {}
    for pid in procfs.iter_pids(proc_root):
        if (stat := procfs.read_stat(pid, proc_root)) is not None:
            members.setdefault(stat.pgid, []).append(pid)
    return members


class MemberIndex:
    """``pgid -> pids`` index kept fresh right before each group's SIGTERM (#904).

    Built by one full ``/proc`` scan; ``members_of`` re-lists ``/proc`` and
    re-reads each live pid's stat (never cmdline), so a process spawned into a
    later group after the batch started is still seen before that group's TERM.
    Each pid is cached as ``(pgid, starttime)`` and a live pid whose identity
    changed — the pid exited and was recycled between refreshes, or moved
    group — is re-indexed as a new process (#982): only starttime proves a pid
    is still the same process, so a mere listing cannot skip the stat read.
    Entries are candidates only — callers re-read each member's stat live.
    """

    def __init__(self, proc_root: Path = procfs.PROC_ROOT) -> None:
        self._proc_root = proc_root
        self._identity_of: dict[int, tuple[int, int]] = {}
        self._by_pgid: dict[int, set[int]] = {}
        self._refresh()

    def _refresh(self) -> None:
        live = set(procfs.iter_pids(self._proc_root))
        for pid in self._identity_of.keys() - live:
            self._forget(pid)
        for pid in live:
            stat = procfs.read_stat(pid, self._proc_root)
            identity = None if stat is None else (stat.pgid, stat.starttime)
            if identity == self._identity_of.get(pid):
                continue
            self._forget(pid)
            if stat is not None:
                self._identity_of[pid] = (stat.pgid, stat.starttime)
                self._by_pgid.setdefault(stat.pgid, set()).add(pid)

    def _forget(self, pid: int) -> None:
        if (cached := self._identity_of.pop(pid, None)) is not None:
            self._by_pgid[cached[0]].discard(pid)

    def members_of(self, pgid: int) -> dict[int, list[int]]:
        """Refresh, then ``{pgid: [pid, ...]}`` in the ``pgid_members`` shape."""
        self._refresh()
        return {pgid: sorted(self._by_pgid.get(pgid, ()))}


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
    proc_root: Path = procfs.PROC_ROOT,
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
    candidates = members.get(pgid, []) if members is not None else procfs.iter_pids(proc_root)
    pinned: set[tuple[int, int]] = set()
    verified = False
    for pid in candidates:
        stat = procfs.read_stat(pid, proc_root)
        if stat is None or stat.pgid != pgid:
            continue
        pinned.add((pid, stat.starttime))
        verified = verified or marker in procfs.read_cmdline(pid, proc_root)
    return GroupIdentity(pgid, marker, frozenset(pinned)) if verified else None


def still_owned(identity: GroupIdentity, proc_root: Path = procfs.PROC_ROOT) -> bool:
    """Re-prove right before a signal that ``identity.pgid`` is still the verified group.

    True while any pinned member still exists with the same starttime inside the
    same pgid (a pgid cannot be recycled while any member — zombies included —
    still holds it). Survivors need not carry the marker: once the marker-bearing
    leader died of SIGTERM, its pinned children are what SIGKILL must still reach.
    """
    if identity.members is None:
        return _ps_group_has_marker(identity.pgid, identity.marker)
    return any(
        (stat := procfs.read_stat(pid, proc_root)) is not None
        and stat.starttime == starttime
        and stat.pgid == identity.pgid
        for pid, starttime in identity.members
    )


def refresh_identity(
    identity: GroupIdentity,
    snapshot: dict[int, list[int]] | None,
    proc_root: Path = procfs.PROC_ROOT,
) -> GroupIdentity | None:
    """Re-pin ``identity`` to its *current* members right before SIGTERM.

    Members spawned after verification (e.g. a TERM-ignoring sandbox child)
    must stay reachable by SIGKILL even when every originally pinned member has
    exited during the TERM wait — under a real init (compose ``init: true`` /
    tini) those exits are reaped at once and vanish from ``/proc``. Current
    members are read first, ownership is re-proven after: a pinned member that
    still exists now also existed at verification, so the group was never
    empty in between and could not have been recycled — every member read is
    therefore ours. None = the group is no longer the verified one.
    """
    if identity.members is None:  # ps fallback: no starttime to pin
        return identity if still_owned(identity, proc_root) else None
    current = {
        (pid, stat.starttime)
        for pid in (snapshot or {}).get(identity.pgid, [])
        if (stat := procfs.read_stat(pid, proc_root)) is not None and stat.pgid == identity.pgid
    }
    if not still_owned(identity, proc_root):
        return None
    return replace(identity, members=identity.members | current)


def group_has_marker(
    pgid: int,
    marker: str,
    proc_root: Path = procfs.PROC_ROOT,
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
