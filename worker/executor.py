#!/usr/bin/env python3
"""Agent Legion Worker: concurrent pull supervisor for Agent/code executions."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from yaml import YAMLError

_PROJECT_ROOT = Path(__file__).resolve().parents[1]  # worker/ 包根
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from worker.cleanup import clean_work_root
from worker.execution.run import agent_subprocess_env as agent_subprocess_env
from worker.execution.run import run_execution
from worker.fd_limits import raise_fd_limit
from worker.host.client import Client, WorkerAuthError
from worker.host.status_sync import sync_host_status
from worker.metrics_cache import WorkerMetricsCache
from worker.registration.retry import register_from_config
from worker.runtime import controls as runtime_controls
from worker.runtime.setup import prepare_runtime_models
from worker.stale_sweep import SWEEP_INTERVAL_SECONDS, sweep_stale_executions
from worker.status import ExecutionStatusReporter
from worker.transfer_controls import claim_availability, load_transfer_controls
from worker.upload.queue import UploadQueue

CLAIM_BACKOFF_CAP_SECONDS = 60.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an Agent Legion Worker")
    parser.add_argument("--config", type=Path, default=Path("config/agent-worker.yaml"))
    args = parser.parse_args()
    try:
        soft, hard = raise_fd_limit()
        print(f"worker fd limit: soft={soft} hard={hard}", flush=True)
    except (OSError, ValueError) as exc:
        print(f"worker fd limit raise failed; continuing with defaults: {exc}", flush=True)
    config = runtime_controls.load_config(args.config)
    max_concurrency, claim_enabled = runtime_controls.load_claim_controls(args.config)
    max_code_concurrency = runtime_controls.load_code_concurrency(args.config)
    if error := prepare_runtime_models(config, code_concurrency=max_code_concurrency):
        # 退出码 2（supervisor 不自动重启）：声明了无法解析二进制的 runtime
        # （自带副本与 PATH 都没有）是部署缺口，重试无意义，必须人工修复后重启。
        print(error, flush=True)
        return 2
    transfer = load_transfer_controls(args.config)
    client = Client(str(config["host_url"]), transfer_timeout=transfer.transfer_timeout_seconds)
    stop = threading.Event()
    status = ExecutionStatusReporter.from_env()
    metrics = WorkerMetricsCache.from_env()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    poll_interval, registration = register_from_config(client, config, stop, args.config.parent)
    if registration is not True:
        return 2 if registration is False else 0
    host_worker: dict[str, Any] | None = {
        "worker_id": str(config["worker_id"]),
        "name": str(config.get("name", config["worker_id"])),
        "revoked": False,
    }
    try:
        host_worker = sync_host_status(client, status, metrics, host_worker)
    except WorkerAuthError as exc:
        print(f"Agent Worker status authentication rejected: {exc}", flush=True)
        return 2
    work_root = Path(str(config.get("work_root", "/var/lib/agent-legion-worker"))).resolve()
    environment = {str(key): str(value) for key, value in config.get("environment", {}).items()}
    interval = float(config.get("heartbeat_interval_seconds", 15))
    shutdown_grace = float(config.get("shutdown_grace_seconds", 25))
    uploads = UploadQueue(
        client,
        status,
        max_concurrency=transfer.upload_max_concurrency,
        heartbeat_interval=interval,
        stop=stop,
    )
    # Restore unreported results BEFORE cleaning: their execution dirs carry
    # an upload_pending.json marker and are preserved by clean_work_root.
    restored = uploads.restore(work_root)
    if restored:
        print(f"restored {restored} pending result upload(s) from {work_root}", flush=True)
    clean_work_root(work_root)
    download_slots = threading.Semaphore(transfer.download_max_concurrency)
    active: set[Future[None]] = set()
    # 双池跟踪（批次 2）：agent/code 各自计数，本地预算避免过度 claim；
    # Host 侧在 claim 事务里再强制一次。
    active_kinds: dict[Future[None], str] = {}
    backoff = poll_interval
    pool = ThreadPoolExecutor(
        runtime_controls.MAX_DYNAMIC_CONCURRENCY, thread_name_prefix="agent-execution"
    )
    next_sweep = time.monotonic()
    next_host_status = time.monotonic() + interval
    control_error: str | None = None
    code_hot_reject_logged = False
    try:
        while not stop.is_set():
            if time.monotonic() >= next_host_status:
                try:
                    host_worker = sync_host_status(client, status, metrics, host_worker)
                except WorkerAuthError as exc:
                    print(
                        f"Agent Worker rejected by server: {exc}; re-register required", flush=True
                    )
                    return 2
                print(
                    f"worker slots {len(active)}/{max_concurrency}+{max_code_concurrency},"
                    f" upload queue depth {uploads.depth}",
                    flush=True,
                )
                next_host_status = time.monotonic() + interval
            if time.monotonic() >= next_sweep:
                sweep_stale_executions(work_root)
                next_sweep = time.monotonic() + SWEEP_INTERVAL_SECONDS
            completed = {future for future in active if future.done()}
            active -= completed
            for future in completed:
                active_kinds.pop(future, None)
                try:
                    future.result()
                except Exception as exc:
                    traceback.print_exc()
                    print(f"Agent execution failed: {exc}", flush=True)
            try:
                new_controls = runtime_controls.load_claim_controls(args.config)
                new_code_concurrency = runtime_controls.load_code_concurrency(args.config)
                new_transfer = load_transfer_controls(args.config)
            except (OSError, ValueError, YAMLError) as exc:
                message = str(exc)
                if message != control_error:
                    print(
                        f"Agent dynamic control reload failed; keeping previous values: {message}",
                        flush=True,
                    )
                    control_error = message
            else:
                # 全部加载成功才统一生效：半应用会让 "keeping previous values"
                # 撒谎（claim 控制已覆盖、transfer 控制还是旧值）。
                max_concurrency, claim_enabled = new_controls
                max_code_concurrency, code_rejected = runtime_controls.hot_code_concurrency(
                    max_code_concurrency, new_code_concurrency
                )
                if code_rejected and not code_hot_reject_logged:
                    print(
                        "max_code_concurrency 0→>0 需要可执行的 velites 二进制"
                        "（启动预检项），热更拒绝生效；请安装 velites 后重启 worker",
                        flush=True,
                    )
                code_hot_reject_logged = code_rejected
                transfer = new_transfer
                uploads.set_max_concurrency(transfer.upload_max_concurrency)
                control_error = None
            agent_active = sum(1 for kind in active_kinds.values() if kind == "agent")
            agent_base = max(0, max_concurrency - agent_active) if claim_enabled else 0
            code_base = (
                max(0, max_code_concurrency - (len(active) - agent_active)) if claim_enabled else 0
            )
            # Backpressure: a deep upload backlog means the Host is not
            # draining results; taper claiming linearly towards zero instead
            # of an all-or-nothing gate, which hysteresis-oscillates.
            budget = {
                "agent": claim_availability(
                    agent_base, uploads.depth, max_concurrency, transfer.upload_backlog_limit
                ),
                "code": claim_availability(
                    code_base,
                    uploads.depth,
                    max(max_code_concurrency, 1),
                    transfer.upload_backlog_limit,
                ),
            }
            claimed = False
            try:
                while budget["agent"] + budget["code"] > 0:
                    if stop.is_set():
                        break
                    claim = client.claim(
                        str(config["worker_id"]), max_concurrency, max_code_concurrency
                    )
                    if claim is None:
                        break
                    claimed = True
                    kind = str(claim.get("kind") or "agent")
                    if kind != "code":
                        kind = "agent"
                    # Host 在 claim 事务里已强制分池；本地预算只防过度
                    # claim，竞态超发时照单收下（Host 已记账）。
                    budget[kind] -= 1
                    future = pool.submit(
                        run_execution,
                        client,
                        claim,
                        work_root,
                        environment,
                        interval,
                        stop,
                        shutdown_grace,
                        status,
                        uploads,
                        download_slots,
                    )
                    active.add(future)
                    active_kinds[future] = kind
            except WorkerAuthError as exc:
                print(f"Agent Worker rejected by server: {exc}; re-register required", flush=True)
                return 2
            except Exception as exc:
                print(f"Agent claim error: {exc}; retrying in {backoff:.1f}s", flush=True)
                stop.wait(backoff)
                backoff = min(backoff * 2, CLAIM_BACKOFF_CAP_SECONDS)
                continue
            backoff = poll_interval
            stop.wait(0.2 if claimed else poll_interval)
    finally:
        stop.set()
        # Bounded: run_execution watches `stop` and kills children within
        # shutdown_grace; upload tasks bail out of retry loops on `stop` and
        # leave their pending markers for the next startup's restore.
        pool.shutdown(wait=True)
        uploads.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
