"""按 lease 的结果提交串行锁（codex #774 P1）。

同一 lease 的并发 /result（网络重投、超时双发）必须串行进入提交临界区
（remote promote + 镜像 + finish 代次闸）：镜像登记走 finish 之前的
lease 写闸、文件落盘走 finish 之内的代次闸，不串行时两道闸的胜者可以
不同——A 镜像、B 镜像、A finish 获胜，本地面=A 而权威面/清单面=B，永
久分叉。串行后到者的镜像写闸看到已释放的 lease 直接拒写
（``lease_artifact_write_current``），所有面只剩获胜者。解包失败的失
败收尾（``completion.finish`` 的转换臂）同样在该临界区内提交（#759
复审 P2）：锁外释放 lease 会让在途成功路径的 finish 落败，其已登记
的产物面与获胜的失败结果分裂。

锁表按 waiters 计数自清（零等待即删），lease id 不随执行量累积。
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass
class _Entry:
    lock: threading.Lock = field(default_factory=threading.Lock)
    waiters: int = 0


class LeaseCompletionLocks:
    """按 key 的互斥锁注册表（线程安全、零等待自清）。"""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._guard = threading.Lock()

    @contextlib.contextmanager
    def acquire(self, lease_id: str) -> Iterator[None]:
        with self._guard:
            entry = self._entries.get(lease_id)
            if entry is None:
                entry = _Entry()
                self._entries[lease_id] = entry
            entry.waiters += 1
        try:
            with entry.lock:
                yield
        finally:
            with self._guard:
                entry.waiters -= 1
                if entry.waiters == 0:
                    del self._entries[lease_id]
