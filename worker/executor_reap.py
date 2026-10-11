"""Reap completed execution futures in the claim loop (split from
``worker/executor.py`` for the file-size budget, #1158 — the exemption text
named the next split as this file's pressure valve).

Behavior is unchanged: the executor's main loop delegates the per-pass reaping
of finished lane futures here.
"""

from __future__ import annotations

import traceback
from concurrent.futures import Future


def reap_completed(active: set[Future[None]], active_kinds: dict[Future[None], str]) -> None:
    """Reap finished futures: extract results (logging failures), keep the loop alive."""
    completed = {future for future in active if future.done()}
    active -= completed
    for future in completed:
        active_kinds.pop(future, None)
        # #534（codex P1 二轮）：越池抑制的解除在预算面（pass_budget
        # 内 avail > 0 的 discard）——执行完成或档位推进都会让预
        # 算转正，此处无需按 kind 解除。
        try:
            future.result()
        except Exception as exc:
            # #204 broad-except audit: 线程池 reap 安全网。执行主体
            # 已在 run_execution 内被遏制（execution/run.py 的
            # prebuilt 降级），能到达这里的只剩 deliver_result 收尾
            # 路径或真正的编程错误——但 claim 轮询循环必须存活：一次
            # future 失败不能让 worker 停摆，该次执行由租约过期后的
            # Host 重调度兜底。吞是对的：这里 future.result() 是异常
            # 的唯一提取点，不捕获则异常已在池内丢失。日志保全：
            # traceback.print_exc() + print 摘要。
            traceback.print_exc()
            print(f"Agent execution failed: {exc}", flush=True)
