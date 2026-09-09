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

_PROJECT_ROOT = Path(__file__).resolve().parents[1]  # worker/ 包根
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from worker import events
from worker.claim_backoff import CLAIM_BACKOFF_CAP_SECONDS, ClaimBackoffSequence
from worker.claim_batch import (
    ClaimRunContext,
    drain_budget,
    load_claim_batch_limit,
)
from worker.claim_budget import pass_budget
from worker.claim_pacing import ClaimPacing
from worker.cleanup import clean_work_root
from worker.fd_limits import raise_fd_limit_startup
from worker.host.client import Client, WorkerAuthError
from worker.host.status_sync import sync_host_status
from worker.hot_controls import DynamicControls, reload_controls
from worker.lease_snapshot import (
    EXECUTOR_RELAY_SYNC_SECONDS,
    executor_relay_sync,
    open_lease_channel,
)
from worker.load_shedding import LoadShedder
from worker.metrics_cache import WorkerMetricsCache
from worker.ramp_up import (
    apply_ramp_hot_reload,
    ramp_pass,
    slots_line,
    validate_ramp_up,
)
from worker.registration.retry import register_from_config
from worker.runtime import controls as runtime_controls
from worker.runtime.controls import MAX_DYNAMIC_CONCURRENCY
from worker.runtime.setup import prepare_runtime_models
from worker.stale_sweep import SWEEP_INTERVAL_SECONDS, sweep_stale_executions
from worker.status import ExecutionStatusReporter
from worker.transfer_controls import load_transfer_controls
from worker.upload.queue import UploadQueue


def _print(message: str) -> None:
    """stdout with flush=True —— worker 子进程日志的统一出口。"""
    print(message, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an Agent Legion Worker")
    parser.add_argument(
        "--config", type=Path, default=Path("data/agent-worker-service/worker.yaml")
    )
    args = parser.parse_args()
    # fd 上限提升（含日志）在 fd_limits 内联；失败不致命（继续用默认值）。
    raise_fd_limit_startup()
    config = runtime_controls.load_config(args.config)
    max_concurrency, claim_enabled, raw_ramp_up = runtime_controls.load_claim_controls(args.config)
    max_code_concurrency = runtime_controls.load_code_concurrency(args.config)
    if error := prepare_runtime_models(config, code_concurrency=max_code_concurrency):
        # 退出码 2（supervisor 不自动重启）：配置无法解析（disabled_runtimes 非法、
        # AGENT_WORKER_EXPECT_RUNTIMES 声明了探测不到的 runtime）或 code 容量缺少
        # velites 沙箱二进制是部署缺口，重试无意义，必须人工修复后重启。
        print(error, flush=True)
        return 2
    # #471 冷启动爬坡：启动预检 fail-fast——非法 ramp_up 块是配置错误，
    # 重试无意义（退出码 2，supervisor 不自动重启，人工修配置后重启）；
    # None（未配置）= 禁用，一次性全量——行为与现状完全一致。
    try:
        ramp_controls = validate_ramp_up(raw_ramp_up)
        # #546：claim_batch_limit 同款启动预检 fail-fast（非法批上限是配置
        # 错误，重试无意义）；缺省 = 默认值，行为见 worker/claim_batch.py。
        claim_batch_limit = load_claim_batch_limit(args.config)
    except ValueError as exc:
        print(f"Agent Worker 启动预检失败：{exc}", flush=True)
        return 2
    transfer = load_transfer_controls(args.config)
    client = Client(str(config["host_url"]), transfer_timeout=transfer.transfer_timeout_seconds)
    stop = threading.Event()
    status = ExecutionStatusReporter.from_env()
    metrics = WorkerMetricsCache.from_env()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())  # noqa: B023
    poll_interval, registration = register_from_config(client, config, stop, args.config.parent)
    if registration is not True:
        return 2 if registration is False else 0
    worker_id = str(config["worker_id"])
    # 首次同步前的兜底视图：get_self 失败时控制台仍有 worker_id 可显示。
    host_worker: dict[str, Any] | None = {"worker_id": worker_id, "revoked": False}
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
    # #352：per-Worker 单心跳循环取代每执行一条心跳线程——本机全部在跑
    # 执行（含排队上传）一次批量续期，写流量按机器数而非槽数；旧 Host
    # （无批量端点）自动降级回逐执行心跳（heartbeat_batch.py）。
    # #566 二期：supervisor 派生时设了快照 env 时心跳 relay 挪到 supervisor
    # 进程（executor 饱和时进程内心跳线程抢不到 GIL），本进程只按拍发布
    # 快照、回收 beat 结果；裸跑（无 env）保持进程内心跳循环。
    heartbeat_registry, lease_snapshot_path = open_lease_channel(client, interval, stop)
    uploads.set_heartbeat_registry(heartbeat_registry)
    # Restore unreported results BEFORE cleaning: their execution dirs carry
    # an upload_pending.json marker and are preserved by clean_work_root.
    if restored := uploads.restore(work_root):
        print(f"restored {restored} pending result upload(s) from {work_root}", flush=True)
    clean_work_root(work_root)
    download_slots = threading.Semaphore(transfer.download_max_concurrency)
    active: set[Future[None]] = set()
    # 双池跟踪（批次 2）：agent/code 各自计数，本地预算防过度 claim（Host
    # 在 claim 事务里再强制）。退避/pacing 的设计记录见各自模块 docstring。
    active_kinds: dict[Future[None], str] = {}
    backoff = ClaimBackoffSequence(cap_seconds=CLAIM_BACKOFF_CAP_SECONDS)
    pacing = ClaimPacing(log=_print)
    # #471：爬坡状态机只管「本 pass 最多领多少」（claim 预算的容量输入），
    # 与 pacing（两次 claim 之间的等待）正交；未配置 ramp_up 块 = None，
    # 预算直通目标——行为与现状完全一致（一次性全量）。
    ramp = apply_ramp_hot_reload(None, ramp_controls, _print)
    # #566 三期：load average 回压（构造即做容量合理性告警）。
    shedder = LoadShedder(max_concurrency, log=_print)
    ramp_view, ramp_paused_since = None, None
    pool = ThreadPoolExecutor(MAX_DYNAMIC_CONCURRENCY, thread_name_prefix="agent-execution")
    # run_execution 的循环不变参数（client/claim 逐单在前，其余两组不变）；
    # uploads/status 实例在本循环内从不重建，热更只调实例内部状态。
    run_args = (work_root, environment, interval, stop, shutdown_grace)
    run_tail = (status, uploads, download_slots)
    next_sweep, next_host_status = time.monotonic(), time.monotonic() + interval
    next_lease_sync, last_beat_seq = time.monotonic(), -1
    control_error = None
    controls = DynamicControls(
        max_concurrency, claim_enabled, max_code_concurrency, transfer, claim_batch_limit, ramp
    )
    # #534（codex P1 二轮）：越池抑制——领到「本地预算已尽的池」的活时
    # 记下该池，抑制期间该池 claim 声明压到当前活跃数（Host 按「active
    # < 声明容量」分池发活，这是唯一能止住逐 pass 再发的通道）、预算视
    # 为 0；该池「未被抑制时的预算」转正（avail > 0：执行完成/档位推进/
    # 背压消退）时解除。否则仅 break 当前 pass 的话，下个 pass 又领一个，
    # running 一路爬到声明容量，绕过 ramp-up/背压。
    pool_deferred: set[str] = set()
    # #546：claim 循环的 loop-invariant 接线（active/active_kinds/
    # pool_deferred 原地可变、身份稳定，随上下文一次构建）。
    claim_ctx = ClaimRunContext(
        client=client,
        worker_id=worker_id,
        pool=pool,
        run_args=run_args,
        run_tail=run_tail,
        heartbeat_registry=heartbeat_registry,
        active=active,
        active_kinds=active_kinds,
        pool_deferred=pool_deferred,
        stop=stop,
    )
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
                # #471：爬坡期 slots 行追加 "ramp-up e/t (+ns)"（判变在
                # 状态机内）；无爬坡时行内容与现状逐字节一致。
                _print(
                    slots_line(
                        len(active), max_concurrency, max_code_concurrency, uploads.depth, ramp_view
                    )
                )
                next_host_status = time.monotonic() + interval
            if time.monotonic() >= next_sweep:
                sweep_stale_executions(work_root)
                next_sweep = time.monotonic() + SWEEP_INTERVAL_SECONDS
            if lease_snapshot_path is not None and time.monotonic() >= next_lease_sync:
                next_lease_sync = time.monotonic() + EXECUTOR_RELAY_SYNC_SECONDS
                last_beat_seq = executor_relay_sync(
                    heartbeat_registry,
                    lease_snapshot_path,
                    worker_id=worker_id,
                    token=client.token,
                    last_result_seq=last_beat_seq,
                )
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
            reloaded, load_error = reload_controls(args.config, controls, uploads, _print)
            if reloaded is None:
                if load_error != control_error:
                    _print(
                        f"Agent dynamic control reload failed; keeping previous values: {load_error}"
                    )
                    control_error = load_error
            else:
                # 全部加载成功才统一生效（语义收口在 hot_controls）。
                controls = reloaded
                control_error = None
            max_concurrency = controls.max_concurrency
            claim_enabled = controls.claim_enabled
            max_code_concurrency = controls.max_code_concurrency
            claim_batch_limit = controls.claim_batch_limit
            ramp = controls.ramp
            # #471：本 pass 生效容量（禁用/未开窗 = 目标直通；暂停期 deduct
            # 折回、enabled 才推进虚拟时钟——策略收口在 ramp_pass）。
            ramp_view, ramp_paused_since = ramp_pass(
                ramp, ramp_paused_since, max_concurrency, time.monotonic(), claim_enabled
            )
            effective = max_concurrency if ramp_view is None else ramp_view.effective
            status.set_ramp_up(ramp_view, max_concurrency)
            # Backpressure（upload 积压线性衰减，见 claim_availability）；
            # #471：worker_capacity 用生效容量（爬坡期=当前档），背压门随档位走。
            # 预算/越池抑制的计算与解除语义收口在 pass_budget（#534）。
            budget, declared = pass_budget(
                active,
                active_kinds,
                effective=effective,
                targets=(max_concurrency, max_code_concurrency),
                claim_enabled=claim_enabled,
                pool_deferred=pool_deferred,
                upload_depth=uploads.depth,
                backlog=controls.transfer.upload_backlog_limit,
            )
            # #546 batch claim：分池批申请（agent_limit/code_limit）+ 总上
            # 限 limit，一次往返领一批——瞬时 code 洪峰不再逐个吃循环节拍；
            # 循环主体（批申请/提交/越池批后记账）在 claim_batch.drain_budget。
            # shed()：load 回压只作用本地预算，声明容量不动（瞬态回压
            # 抖进 Host 记账会放大振荡）。
            claimed, claim_rtt, budget = False, 0.0, shedder.shed(budget)
            try:
                claimed, claim_rtt = drain_budget(
                    claim_ctx, budget, declared, claim_batch_limit, uploads.depth, claim_enabled
                )
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
                # #490 claim.backoff：#437 序列状态结构化落盘；HTTP 错误码/
                # URL 已在 client.request 的 http.error 事件里。
                events.note_claim_backoff(worker_id, exc, wait, backoff.failures)
                print(f"Agent claim error: {exc}; retrying in {wait:.1f}s", flush=True)
                stop.wait(wait)
                continue
            backoff.reset()
            # #472：成功=自适应短等待，空队列=poll_interval；错误路径走 backoff。
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
