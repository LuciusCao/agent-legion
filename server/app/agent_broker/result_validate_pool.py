"""Process-pool offload for the result validate segment (issue #569).

Sibling of ``result_unpack_pool`` (#552), deliberately NOT the same pool:
the task profiles differ — an unpack is milliseconds of pure CPU, while a
validation parks on subprocess waits (the velites contract engine and the
legacy ``validate_output.py``, each with a 30s timeout), so sharing one
pool would let a slow validation head-of-line block every unpack behind it.

The split point keeps the DB-touching half in the main process: a legacy
manifest's ``skill_ref`` is resolved to a commit there (it may read/write
the skill lock document); the pool task (``validate_skill_commit_outputs``)
is a pure path-in/string-out transform — materialize the (skill, commit)
pair through the shared cache (``skills.commit_cache``, zero git calls on a
hit), copy out a per-validation private tree under the same FileLock,
contract-check it, run the two-layer validator against it. The task must
stay an importable module-level function with picklable args/return (the
spawn-context constraint); exceptions cross the boundary pickled by
reference, so ``SkillRepoError`` keeps its type for the caller's
convert-to-contract containment.

Pool size: ``AGENT_LEGION_RESULT_VALIDATE_WORKERS`` overrides the instance
setting ``result_validate.workers`` (admin UI, restart-effective via
``configure`` at startup hydration; 0 = unset), which overrides the default
min(4, cpu_count) — same precedence chain as the unpack pool (#554).

Pool recovery mirrors the unpack pool: a pool worker CAN die hard (validator
subprocess OOM, native crash), after which every submit raises
``BrokenProcessPool`` forever — ``validate_in_pool`` rebuilds the pool once
and retries the task; a deterministically crashing validation fails THIS
result (the caller's containment converts it), not the pipeline.
"""

from __future__ import annotations

import multiprocessing
import os
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

_POOL: ProcessPoolExecutor | None = None
_POOL_LOCK = threading.Lock()
# Instance-settings value: set once by configure() at startup hydration;
# 0 = unset. The pool is created lazily on the first result, so startup
# configuration always lands before pool creation.
_CONFIGURED_WORKERS = 0


def configure(workers: int) -> None:
    """Pin the pool size from the instance document (0 = back to auto/env)."""
    global _CONFIGURED_WORKERS
    _CONFIGURED_WORKERS = workers


def _pool_size() -> int:
    override = os.environ.get("AGENT_LEGION_RESULT_VALIDATE_WORKERS", "")
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            # 配置错误不该把每条 result 都判 failed——warn（每次解析池尺寸
            # 时各一次，即每次建池一次）并回落默认。
            import logging

            logging.getLogger(__name__).warning(
                "AGENT_LEGION_RESULT_VALIDATE_WORKERS=%r 非法，回落实例设置/自动池大小",
                override,
            )
    if _CONFIGURED_WORKERS > 0:
        return _CONFIGURED_WORKERS
    return min(4, os.cpu_count() or 1)


def _new_pool() -> ProcessPoolExecutor:
    # spawn（而非 Linux 默认的 fork）：与 unpack 池同理，多线程 server 进程
    # 的惰性 fork 会继承父进程锁状态；子进程只 import 纯路径模块，一次性
    # 成本可忽略。
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
    """丢弃当前池（下次提交重建）。``broken`` 身份守卫与 wait=True 语义同
    unpack 池：只关停调用者实际撞破的那个池；worker 进程退出同步等完。"""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None or (broken is not None and _POOL is not broken):
            return
        _POOL.shutdown(wait=True, cancel_futures=True)
        _POOL = None


def validate_in_pool(call: Callable[..., Any], *args: Any) -> Any:
    """Run ``call(*args)`` on the pool and return/raise its outcome.

    The callable must be an importable module-level function with picklable
    args/return (the spawn-context constraint). A broken pool is rebuilt
    once and the task retried; a deterministically crashing validation fails
    THIS result (the caller's containment converts it), not the pipeline.
    """
    pool = _pool()
    try:
        return pool.submit(call, *args).result()
    except BrokenProcessPool:
        reset_pool(broken=pool)
        return _pool().submit(call, *args).result()


def validate_skill_commit_outputs(
    base_dir: str,
    runs_dir: str,
    git_command: tuple[str, ...],
    skill_key: str,
    commit: str,
    job_dir: str,
) -> str | None:
    """Pool task: validate ``job_dir`` against a private copy of (skill, commit).

    Runs in a pool worker: no DB handle, no shared process state — the
    manager is rebuilt from plain path/string args with a NullSkillStore
    (the exact-commit path never touches the skill lock). The shared cache
    tree is read-only; the validator runs against a per-validation private
    copy (PR #571 codex P1s: a validator writing beside its own
    ``__file__`` must not pollute the cache, and LRU eviction must never
    rmtree a tree mid-validation — the copy window and eviction share the
    per-repo FileLock, and the validator reads only the private copy).
    Returns the validator verdict (None = valid); raises cross the process
    boundary (materialization/contract failures) for the caller to convert.
    """
    # Local imports: keeps the spawn child's import graph minimal and lets
    # the pool module itself stay cheap to import in the main process.
    import uuid

    from server.app.skills.commit_cache import (
        NullSkillStore,
        materialized_private_copy,
    )
    from server.app.skills.manager import SkillManager
    from server.app.workflows.output_validation import run_output_validator
    from server.app.workflows.skills import resolve_workflow_skill

    manager = SkillManager(
        store=NullSkillStore(),
        base_dir=Path(base_dir),
        runs_dir=Path(runs_dir),
        git_command=list(git_command),
    )
    validation_id = f"validate-{uuid.uuid4().hex}"
    run_dir = materialized_private_copy(manager, skill_key, commit, validation_id)
    try:
        # Contract-check the private copy (same bar as the dispatch path):
        # <runs_dir>/<validation_id>/<workflow>/<capability>, so parents[1]
        # is the root the key joins under.
        resolve_workflow_skill(run_dir.parents[1], skill_key)
        return run_output_validator(run_dir, Path(job_dir))
    finally:
        # Per-validation cleanup is back (#569's original design removed it
        # with the per-validation dir); a pool worker dying hard leaks the
        # copy to the age-based sweeper instead.
        manager.cleanup_execution(validation_id)
