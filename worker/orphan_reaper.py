"""Orphaned execution process-group reaping (supervisor crash-recovery path).

Both execution kinds record their child pgid (``AGENT_PGID_FILENAME``): agent
runs from ``execution_run`` and kind='code' runs from ``code_runner``. Identity
is verified through the ``agent-legion-<execution_id>`` argv marker, injected
by the agent command builders as ``--name`` and by
``shared/code_sandbox.build_sandbox_argv`` as a trailing argv element (#186).

#682: the marker check reads ``/proc`` (slim worker images have no ``ps``,
which used to discard every record as unverifiable and leave the orphan
groups unmanaged); ``ps`` remains the fallback where ``/proc`` is absent
(macOS dev). After the kill, exited group members that were reparented to
this process (the supervisor running as container PID 1) are wait()ed so
the kill does not just turn them into zombies.
"""

from __future__ import annotations

import contextlib
import os
import signal
import time
from pathlib import Path

from worker import procfs
from worker.process_lifecycle import AGENT_PGID_FILENAME
from worker.zombie_reaper import COLLECT_TIMEOUT_SECONDS, collect_group


def _group_has_agent_marker(
    pgid: int, marker: str, members: dict[int, list[int]] | None = None
) -> bool:
    """True when a live process in `pgid` carries the expected agent argv marker."""
    return procfs.group_has_marker(pgid, marker, members=members)


def reap_orphaned_agents(work_root: Path, log=print) -> None:
    # SIGTERM→短等待→SIGKILL 清理记录残留的 agent 进程组（ESRCH/EPERM 忽略）。
    # #682 两阶段：先逐条校验身份，再整批 TERM → 一次等待 → 整批 KILL → 收割；
    # 逐条 sleep(1) 在一次 executor 被杀遗留数百条记录时会把 supervisor 卡住数分钟。
    members = procfs.pgid_members()  # 一次 /proc 全表快照（无 /proc 时 None，走 ps）
    targets: list[tuple[Path, int]] = []
    for record in work_root.glob(f"*/{AGENT_PGID_FILENAME}"):
        with contextlib.suppress(OSError, ValueError):
            pgid = int(record.read_text(encoding="utf-8"))
            if pgid <= 1 or pgid == os.getpgrp():
                # 半截/恶意记录：0/-1 或本进程组会把信号发给自己，按垃圾跳过
                continue
            # pgid 在宿主重启/崩溃后可能被 OS 回收复用：裸 pgid 发信号会误杀
            # 无关进程组。仅当组内仍有携带本 execution 标记（agent 路径经
            # 命令构建器注入 --name agent-legion-<execution_id>；code 路径经
            # shared/code_sandbox.build_sandbox_argv 注入 argv 尾部标记）的
            # 进程时才发信号；无法确认身份的陈旧记录只清理记录本身。
            # 快照只给出候选成员，标记按成员当前的 stat/cmdline 现读校验。
            marker = f"agent-legion-{record.parent.name}"
            if not _group_has_agent_marker(pgid, marker, members):
                record.unlink(missing_ok=True)
                log(f"discarded unverifiable agent pgid record {pgid} ({record.parent.name})")
                continue
            targets.append((record, pgid))
    if not targets:
        return
    _signal_groups(targets, signal.SIGTERM)
    time.sleep(1)
    _signal_groups(targets, signal.SIGKILL)
    deadline = time.monotonic() + COLLECT_TIMEOUT_SECONDS
    for record, pgid in targets:
        record.unlink(missing_ok=True)
        collected = collect_group(pgid, max(0.0, deadline - time.monotonic()))
        log(f"reaped orphaned agent process group {pgid} (collected {collected})")


def _signal_groups(targets: list[tuple[Path, int]], signum: signal.Signals) -> None:
    for _record, pgid in targets:
        with contextlib.suppress(OSError):
            os.killpg(pgid, signum)
