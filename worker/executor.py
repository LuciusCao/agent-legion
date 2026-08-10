#!/usr/bin/env python3
"""Agent Legion Worker: concurrent pull supervisor for Pi/OpenClaw Agent executions."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from yaml import YAMLError

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from worker import runtime_controls
from worker._atomic import atomic_write
from worker.cleanup import clean_work_root
from worker.event_filter import spawn_event_pump
from worker.execution_heartbeat import start_lease_heartbeat
from worker.execution_prepare import prepare_execution
from worker.fd_limits import raise_fd_limit
from worker.host_client import Client, WorkerAuthError
from worker.host_status_sync import sync_host_status
from worker.metrics_cache import WorkerMetricsCache
from worker.process_lifecycle import AGENT_PGID_FILENAME, terminate, wait_for_exit
from worker.registration_retry import register_from_config
from worker.runtime_preflight import preflight_error
from worker.stale_sweep import SWEEP_INTERVAL_SECONDS, sweep_stale_executions
from worker.status import ExecutionStatusReporter
from worker.transfer_controls import claim_availability, load_transfer_controls
from worker.upload_queue import MAX_ERROR_MESSAGE_CHARS, UploadQueue, UploadTask

CLAIM_BACKOFF_CAP_SECONDS = 60.0
load_claim_controls = runtime_controls.load_claim_controls


def agent_subprocess_env(environment: dict[str, str]) -> dict[str, str]:
    """Env for one agent subprocess: worker env + config overrides."""
    # Prepend the worker interpreter's bin dir (its own venv, carrying
    # `python`/`python3`) to PATH so agent bash sessions resolve the
    # interpreter regardless of the launch context; otherwise agents hunt it
    # with full-disk `find /` scans that flood fseventsd/Spotlight. The path
    # stays unresolved on purpose: resolving the `.venv/bin/python` symlink
    # would point at the base installation and bypass the venv.
    env = {**os.environ, **environment}
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    # LLM gateway token: pi reads it via the provider's apiKey
    # "$LLM_GATEWAY_TOKEN" interpolation; keep the worker env authoritative.
    env.pop("LLM_GATEWAY_TOKEN", None)
    if gateway_token := os.environ.get("LLM_GATEWAY_TOKEN", ""):
        env["LLM_GATEWAY_TOKEN"] = gateway_token
    return env


def run_execution(
    client: Client,
    claim: dict[str, Any],
    work_root: Path,
    environment: dict[str, str],
    heartbeat_interval: float,
    shutdown: threading.Event,
    shutdown_grace: float,
    status: ExecutionStatusReporter,
    uploads: UploadQueue,
    download_slots: threading.Semaphore,
) -> None:
    """Run one claimed execution and hand its result to the upload queue.

    The execution slot (this thread) is occupied only by work that needs the
    Agent itself: download inputs, run the process. Everything after process
    exit — compression, archive, upload, report — belongs to the UploadQueue,
    and the Host-side slot is released via release-slot right at exit."""
    execution_id = str(claim["execution_id"])
    lease_id = str(claim["lease_id"])
    node_key = str(claim["node_key"])
    execution_dir = work_root / execution_id
    job_dir = execution_dir / "job"
    run_dir = job_dir / "runs" / node_key / "worker"
    # agent 进程组记录：executor 被 SIGKILL 时 supervisor 按此 killpg 兜底。
    pgid_record = execution_dir / AGENT_PGID_FILENAME
    status_fields = {
        "job_id": str(claim.get("job_id", "")),
        "node_key": node_key,
        "workflow_key": str(claim.get("workflow_key", "")),
        "agent_id": str(claim.get("agent_id", "")),
        "run_dir": str(run_dir),
    }
    status.start(execution_id, **status_fields)
    ownership_lost = threading.Event()
    heartbeat = start_lease_heartbeat(
        client, execution_id, lease_id, heartbeat_interval, ownership_lost
    )
    proc: subprocess.Popen[bytes] | None = None
    task: UploadTask | None = None
    try:
        if shutdown.is_set():
            task = UploadTask(
                execution_id=execution_id,
                lease_id=lease_id,
                execution_dir=execution_dir,
                node_key=node_key,
                status_fields=status_fields,
                kind="prebuilt",
                prebuilt_metadata={
                    "status": "cancelled",
                    "exit_code": 130,
                    "error_message": "Agent Worker is shutting down",
                    "command": [],
                },
            )
        else:
            status.set_phase(execution_id, "downloading")
            prepared = prepare_execution(client, claim, execution_dir, download_slots)
            manifest = prepared.manifest
            command = prepared.command
            events = run_dir / "events.jsonl"
            env = agent_subprocess_env(environment)
            status.set_phase(execution_id, "running")
            with events.open("wb") as output:
                proc = subprocess.Popen(
                    command,
                    cwd=job_dir,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                atomic_write(pgid_record, str(proc.pid))
                heartbeat.proc_ref["proc"] = proc
                # Drop token-delta spam as it streams by; deltas are discarded at upload time anyway.
                pump = spawn_event_pump(proc, output, f"pi-events-{execution_id[:8]}")
                # Fallback aligns with the Host product constant
                # (dispatch.EXECUTION_TIMEOUT_SECONDS = 1800); manifests
                # always carry timeout_seconds, so this only covers
                # hand-built/legacy manifests.
                timeout = float(manifest.get("execution", {}).get("timeout_seconds", 1800))
                exit_code, report_result = wait_for_exit(
                    proc, timeout, shutdown, shutdown_grace, ownership_lost
                )
                pump.join(timeout=10)
            if report_result:
                task = UploadTask(
                    execution_id=execution_id,
                    lease_id=lease_id,
                    execution_dir=execution_dir,
                    node_key=node_key,
                    status_fields=status_fields,
                    kind="process",
                    exit_code=exit_code,
                    expected_outputs=tuple(
                        str(name) for name in manifest.get("expected_outputs", [])
                    ),
                    command=tuple(command),
                )
            # else: lease lost mid-run — the Host owns the outcome; nothing
            # to deliver, fall through to the local-discard path below.
    except Exception as exc:
        traceback.print_exc()
        task = UploadTask(
            execution_id=execution_id,
            lease_id=lease_id,
            execution_dir=execution_dir,
            node_key=node_key,
            status_fields=status_fields,
            kind="prebuilt",
            prebuilt_metadata={
                "status": "failed",
                "exit_code": 1,
                "error_message": str(exc)[:MAX_ERROR_MESSAGE_CHARS],
            },
        )
    finally:
        if proc is not None and proc.poll() is None:
            terminate(proc, 5)
        pgid_record.unlink(missing_ok=True)
    if task is not None:
        # Free the Host-side execution slot BEFORE queueing the upload: from
        # here on the remaining work is pure I/O. 404 = Host predates the
        # endpoint (slot held until report, the old behavior); 409 = lease
        # gone, the result is moot — discard instead of uploading.
        try:
            released = client.release_slot(execution_id, lease_id)
        except Exception as exc:
            print(
                f"release-slot failed for {execution_id}: {exc}; slot held until report",
                flush=True,
            )
        else:
            if released == 409:
                task = None
        if task is not None:
            task.heartbeat_stop = heartbeat.stop
            task.heartbeat_thread = heartbeat.thread
            heartbeat.adopt()
            try:
                uploads.submit(task)
            except Exception:
                heartbeat.stop.set()  # 停止租约心跳线程，避免泄漏
                heartbeat.adopted.clear()
                raise
            return
    # Local discard: lease lost or release rejected — stop heartbeating and
    # drop the execution dir; the Host requeues after the lease expires.
    heartbeat.stop.set()
    heartbeat.thread.join(timeout=2)
    status.finish(execution_id)
    shutil.rmtree(execution_dir, ignore_errors=True)


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
    if error := preflight_error(config.get("runtimes") or []):
        # 退出码 2（supervisor 不自动重启）：声明了 PATH 上没有二进制的
        # runtime 是部署缺口，重试无意义，必须人工修复后重启。
        print(error, flush=True)
        return 2
    transfer = load_transfer_controls(args.config)
    client = Client(str(config["host_url"]), transfer_timeout=transfer.transfer_timeout_seconds)
    stop = threading.Event()
    status = ExecutionStatusReporter.from_env()
    metrics = WorkerMetricsCache.from_env()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    poll_interval, registration = register_from_config(client, config, stop)
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
    max_concurrency, claim_enabled = runtime_controls.load_claim_controls(args.config)
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
    backoff = poll_interval
    pool = ThreadPoolExecutor(
        runtime_controls.MAX_DYNAMIC_CONCURRENCY, thread_name_prefix="agent-execution"
    )
    next_sweep = time.monotonic()
    next_host_status = time.monotonic() + interval
    control_error: str | None = None
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
                    f"worker slots {len(active)}/{max_concurrency},"
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
                try:
                    future.result()
                except Exception as exc:
                    traceback.print_exc()
                    print(f"Agent execution failed: {exc}", flush=True)
            try:
                new_controls = runtime_controls.load_claim_controls(args.config)
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
                transfer = new_transfer
                uploads.set_max_concurrency(transfer.upload_max_concurrency)
                control_error = None
            base = max(0, max_concurrency - len(active)) if claim_enabled else 0
            # Backpressure: a deep upload backlog means the Host is not
            # draining results; taper claiming linearly towards zero instead
            # of an all-or-nothing gate, which hysteresis-oscillates.
            available = claim_availability(
                base, uploads.depth, max_concurrency, transfer.upload_backlog_limit
            )
            claimed = False
            try:
                for _ in range(available):
                    if stop.is_set():
                        break
                    claim = client.claim(str(config["worker_id"]), max_concurrency)
                    if claim is None:
                        break
                    claimed = True
                    active.add(
                        pool.submit(
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
                    )
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
