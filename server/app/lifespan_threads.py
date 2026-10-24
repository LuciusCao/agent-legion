"""Lifespan 线程的成对关停（main.py file budget 拆分，#1316）。

main.py 的 finally 必须在池关闭前按固定顺序停线程：sweeper、slow sweeps、
存量材料 ETag binder（#1316，独立于 sweeper_enabled 门控，见
services/material_legacy_etag_binding.py）先停，workflow worker 最后
（unregister_wakeup 与 worker_startup.register_wakeup 配对）。启动侧留在
main.py 的 lifespan 里（启动顺序与 #591 batcher 纪律耦合）；关停是纯机械
逆序，抽出以给 composition root 腾 budget。
"""

from __future__ import annotations

from server.app.executors.sweeper import SweeperThread
from server.app.scheduler_wakeup import unregister_wakeup
from server.app.services.material_legacy_etag_binding import LegacyMaterialEtagBinderThread
from server.app.sweeper_owned_startup import SweeperOwnedThreads
from server.app.workflow_worker.thread import WorkflowWorkerThread


def stop_lifespan_threads(
    sweeper_thread: SweeperThread | None,
    slow_sweeps: SweeperOwnedThreads | None,
    legacy_etag_binder: LegacyMaterialEtagBinderThread | None,
    workflow_worker_thread: WorkflowWorkerThread | None,
) -> None:
    """停 lifespan 启动的全部线程（None 跳过；workflow worker 最后）。"""
    for thread in (sweeper_thread, *(slow_sweeps or ()), legacy_etag_binder):
        if thread is not None:
            thread.stop()
    if workflow_worker_thread is not None:
        unregister_wakeup(workflow_worker_thread.wake)
        workflow_worker_thread.stop()
