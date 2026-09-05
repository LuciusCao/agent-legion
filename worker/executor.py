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

from worker.claim_backoff import CLAIM_BACKOFF_CAP_SECONDS, ClaimBackoffSequence
from worker.claim_pacing import ClaimPacing
from worker.cleanup import clean_work_root
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an Agent Legion Worker")
    parser.add_argument(
        "--config", type=Path, default=Path("data/agent-worker-service/worker.yaml")
    )
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
        # 退出码 2（supervisor 不自动重启）：配置无法解析（disabled_runtimes 非法、
        # AGENT_WORKER_EXPECT_RUNTIMES 声明了探测不到的 runtime）或 code 容量缺少
        # velites 沙箱二进制是部署缺口，重试无意义，必须人工修复后重启。
        print(error, flush=True)
        return 2
    transfer = load_transfer_controls(args.config)
    client = Client(str(config["host_url"]), transfer_timeout=transfer.transfer_timeout_seconds)
    stop = threading.Event()
    status = ExecutionStatusReporter.from_env()
    metrics = WorkerMetricsCache.from_env()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    poll_interval, registration = register_from_config(client, config, stop, args.config.parent)
    if registration is not True:
        return 2 if registration is False else 0
    # 首次同步前的兜底视图：get_self 失败时控制台仍有 worker_id 可显示。
    host_worker: dict[str, Any] | None = {"worker_id": str(config["worker_id"]), "revoked": False}
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
    # #437: 退避序列改为「首次 1s 固定 → 指数翻倍 ±20% jitter → 上限 60s」。
    # 旧版以 poll_interval 为基数且无 jitter：Host 一次抖动让整条领料线同步
    # 退避对齐（锯齿并发）；首退避短固定让瞬时抖动不烧掉一个完整 poll 周期。
    # #472：成功路径 0.2s 固定等待 → 自适应短等待（ClaimPacing 模块）。
    backoff = ClaimBackoffSequence(cap_seconds=CLAIM_BACKOFF_CAP_SECONDS)
    pacing = ClaimPacing(log=lambda message: print(message, flush=True))
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
                        "max_code_concurrency 0→>0 需要可解析的沙箱包装器（velites-sandbox"
                        " 或 velites，启动预检项），热更拒绝生效；docker 形态该包装器内置"
                        "镜像（此错误通常意味着镜像损坏），裸机请安装后重启 worker",
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
            claim_rtt = 0.0
            try:
                while budget["agent"] + budget["code"] > 0:
                    if stop.is_set():
                        break
                    claim_started = time.monotonic()
                    claim = client.claim(
                        str(config["worker_id"]), max_concurrency, max_code_concurrency
                    )
                    if claim is None:
                        break
                    # #472 codex P2：pacing 输入是单次成功 claim 的往返——
                    # 批量 pass 里逐次重打点。批次总墙钟（N 次 claim + N 次
                    # submit）不进 pacing，否则爬坡段输入被放大、直接钳死
                    # 上沿，「成功=往返×0.5」退化为固定 100ms。
                    claim_rtt = time.monotonic() - claim_started
                    claimed = True
                    kind = "code" if str(claim.get("kind")) == "code" else "agent"
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
                # #204 broad-except audit: claim 轮询的存活语义。try 体的
                # 逃逸族混族——client.claim 的传输错误（requests 族）、非 200
                # 状态的 RuntimeError、应答解码的 ValueError——统一语义都是
                # "Host 暂时不可用"，唯一正确响应是指数退避（带上限）后重试；
                # WorkerAuthError 是终态，已在上一臂单独 return 2。吞是对的：
                # 主循环死亡 = worker 停摆。结果空间是本轮 claim 空转一次，
                # 已提交的 future 不受影响。日志保全：print 记录异常与退避
                # 时长。#437：等待时长经 ClaimBackoffSequence（首 1s 固定、
                # 之后指数翻倍 ±20% jitter、上限 60s），fleet 不同步对齐。
                wait = backoff.next_wait()
                print(f"Agent claim error: {exc}; retrying in {wait:.1f}s", flush=True)
                stop.wait(wait)
                continue
            backoff.reset()
            # #472：三路径收口进 wait_after_pass——成功=自适应短等待
            # （单次往返），空队列=poll_interval；错误路径在上面 except
            # 臂走 backoff。
            stop.wait(pacing.wait_after_pass(claimed, claim_rtt, poll_interval))
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
