"""Orphaned agent process-group reaping (supervisor crash-recovery path)."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from pathlib import Path

from worker.process_lifecycle import AGENT_PGID_FILENAME


def _group_has_agent_marker(pgid: int, marker: str) -> bool:
    """True when a live process in `pgid` carries the expected agent argv marker."""
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


def reap_orphaned_agents(work_root: Path, log=print) -> None:
    # SIGTERM→短等待→SIGKILL 清理记录残留的 agent 进程组（ESRCH/EPERM 忽略）。
    for record in work_root.glob(f"*/{AGENT_PGID_FILENAME}"):
        with contextlib.suppress(OSError, ValueError):
            pgid = int(record.read_text(encoding="utf-8"))
            if pgid <= 1:  # 半截/恶意记录：0/-1 会把信号发给本进程组，按垃圾跳过
                continue
            # pgid 在宿主重启/崩溃后可能被 OS 回收复用：裸 pgid 发信号会误杀
            # 无关进程组。仅当组内仍有携带本 execution 标记（两个 runtime 的
            # 命令构建器都注入 --name agent-legion-<execution_id>）的进程时才
            # 发信号；无法确认身份的陈旧记录只清理记录本身。
            marker = f"agent-legion-{record.parent.name}"
            if not _group_has_agent_marker(pgid, marker):
                record.unlink(missing_ok=True)
                log(f"discarded unverifiable agent pgid record {pgid} ({record.parent.name})")
                continue
            with contextlib.suppress(OSError):
                os.killpg(pgid, signal.SIGTERM)
                time.sleep(1)
                os.killpg(pgid, signal.SIGKILL)
            record.unlink(missing_ok=True)
            log(f"reaped orphaned agent process group {pgid}")
