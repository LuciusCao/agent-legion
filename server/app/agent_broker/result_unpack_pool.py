"""Process-pool offload for the result unpack CPU segment (issue #552).

The result commit path runs on the HTTP plane's threadpool: the tar/gzip
unpack, member validation and expected-output extraction in
``unpack_agent_result`` are pure-CPU work bound by the GIL — a completion
wave over the single-core ceiling (实测个位数 result/s) queues every result
POST and pushes lease TTLs to the edge. The function is a pure path-in/
path-out transform (no DB handle, no shared process state), so it drops
into a ``ProcessPoolExecutor`` unchanged: the calling thread parks in
``future.result()`` (a GIL-releasing C wait) while N cores unpack in
parallel. Crash isolation is a bonus: a malformed archive kills a pool
worker, never the HTTP plane.

Pool size: ``AGENT_LEGION_RESULT_UNPACK_WORKERS`` overrides the default
min(4, cpu_count) — the issue's conservative starting point; the event loop
keeps headroom and the DB side (finish/mark_done) stays in-process.

Pool recovery: the archives are untrusted Worker uploads, so a pool worker
CAN die hard (zip-bomb OOM, native crash) — after that every submit raises
``BrokenProcessPool`` forever. ``unpack_in_pool`` therefore rebuilds the
pool once and retries the task; the single retry bounds the blast radius
of a poisoned archive to one failed result (the retry is a fresh worker
process — a deterministically crashing archive fails the result, not the
pool).
"""

from __future__ import annotations

import multiprocessing
import os
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any

_POOL: ProcessPoolExecutor | None = None
_POOL_LOCK = threading.Lock()


def _pool_size() -> int:
    override = os.environ.get("AGENT_LEGION_RESULT_UNPACK_WORKERS", "")
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            # 配置错误不该把每条 result 都判 failed——warn 一次并回落默认。
            import logging

            logging.getLogger(__name__).warning(
                "AGENT_LEGION_RESULT_UNPACK_WORKERS=%r 非法，回落默认池大小", override
            )
    return min(4, os.cpu_count() or 1)


def _new_pool() -> ProcessPoolExecutor:
    # spawn（而非 Linux 默认的 fork）：多线程 server 进程的惰性 fork 会继承
    # 父进程锁状态（3.12+ 对此发 DeprecationWarning）；子进程只 import 纯路径
    # 模块（result_unpack 链无 import 期副作用），一次性成本可忽略。
    return ProcessPoolExecutor(
        max_workers=_pool_size(), mp_context=multiprocessing.get_context("spawn")
    )


def _pool() -> ProcessPoolExecutor:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = _new_pool()
        return _POOL


def reset_pool(broken: ProcessPoolExecutor | None = None) -> None:
    """丢弃当前池（下次提交重建）。``broken`` 身份守卫：只关停调用者实际
    撞破的那个池——完成波下多条线程会同时撞 BrokenProcessPool（池内
    worker 硬死时所有 pending future 同时失败），后来者的 reset 若落在别
    人刚建好的新池上，会把人家的重试 future 一起 cancel 掉。"""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None or (broken is not None and _POOL is not broken):
            return
        _POOL.shutdown(wait=False, cancel_futures=True)
        _POOL = None


def unpack_in_pool(call: Callable[..., Any], *args: Any) -> Any:
    """Run ``call(*args)`` on the pool and return/raise its outcome.

    The callable must be an importable module-level function with picklable
    args/return (the spawn-context constraint). Exceptions cross the process
    boundary pickled by reference — ``AgentBundleError`` keeps its type.
    A broken pool (a pool worker died on an untrusted archive) is rebuilt
    once and the task retried; a deterministically crashing archive fails
    THIS result (the caller's containment converts it), not the pipeline.
    """
    pool = _pool()
    try:
        return pool.submit(call, *args).result()
    except BrokenProcessPool:
        reset_pool(broken=pool)
        return _pool().submit(call, *args).result()
