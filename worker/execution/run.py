"""One claimed execution's run: prepare, spawn, wait, and hand off the result.

Split out of ``worker.executor`` for the file-size budget (mirrors the
Host-side ``_code_sandbox.py`` split pattern): the executor module keeps the
claim supervisor loop, this module owns the per-execution lifecycle for both
kinds — ``agent`` (Pi/OpenClaw/velites runtime subprocess) and, since batch 2,
``code`` (node code through the velites sandbox via ``worker.code_runner``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

from worker._atomic import atomic_write
from worker.code_runner import cancel_executions, execute_code
from worker.event_filter import spawn_event_pump
from worker.execution.heartbeat import ExecutionHeartbeat, start_lease_heartbeat
from worker.execution.prepare import prepare_execution
from worker.host.client import Client
from worker.process_lifecycle import AGENT_PGID_FILENAME, terminate, wait_for_exit
from worker.status import ExecutionStatusReporter
from worker.upload.queue import (
    MAX_ERROR_MESSAGE_CHARS,
    PENDING_FILENAME,
    PendingUploadExists,
    UploadQueue,
    UploadTask,
)


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


def deliver_result(
    client: Client,
    uploads: UploadQueue,
    status: ExecutionStatusReporter,
    task: UploadTask | None,
    heartbeat: ExecutionHeartbeat,
    execution_dir: Path,
    execution_id: str,
) -> None:
    """Shared post-exit tail: free the Host slot, then submit or discard."""
    if task is not None:
        # Free the Host-side execution slot BEFORE queueing the upload: from
        # here on the remaining work is pure I/O. 404 = Host predates the
        # endpoint (slot held until report, the old behavior); 409 = lease
        # gone, the result is moot — discard instead of uploading.
        try:
            released = client.release_slot(execution_id, task.lease_id)
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
    # #203：带未投递 marker 的目录归 UploadQueue 所有，保留待其投递后自清
    # （skip 分支的 marker 必属当前 lease；孤儿 marker 在 prepare 已随 stale
    # 目录清掉，走不到这里）。
    heartbeat.stop.set()
    heartbeat.thread.join(timeout=2)
    status.finish(execution_id)
    if not (execution_dir / PENDING_FILENAME).is_file():
        shutil.rmtree(execution_dir, ignore_errors=True)


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
    # Batch 2: kind='code' claims run the node code sandboxed instead of an
    # Agent runtime; absent kind = agent (old Hosts never send it).
    exec_kind = str(claim.get("kind") or "agent")
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
        "run_dir": "" if exec_kind == "code" else str(run_dir),
    }
    status.start(execution_id, **status_fields)
    ownership_lost = threading.Event()
    heartbeat = start_lease_heartbeat(
        client,
        execution_id,
        lease_id,
        heartbeat_interval,
        ownership_lost,
        # 任一心跳线程都可能带回 Host 的 code 取消列表（协议 v2 body）。
        on_cancelled=cancel_executions,
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
        elif exec_kind == "code":
            status.set_phase(execution_id, "downloading")
            task = execute_code(
                client,
                claim,
                execution_dir,
                status_fields,
                download_slots,
                shutdown,
                shutdown_grace,
                ownership_lost,
                heartbeat,
                status,
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
                    # #160 D12：直传 S3 的上传规格（空 = 旧 CAS 通道）。
                    artifact_uploads=dict(manifest.get("artifact_uploads") or {}),
                )
            # else: lease lost mid-run — the Host owns the outcome; nothing
            # to deliver, fall through to the local-discard path below.
    except PendingUploadExists:
        # #203：execution_dir 属于本 claim 租约的排队中 pending 上传。上报假
        # failed 会经 submit() 覆盖 marker 丢掉旧结果，所以本次 claim 直接放
        # 弃：task 保持 None 走本地丢弃分支（marker 目录被豁免），停心跳让租
        # 约到期，由 Host 重新调度。孤儿 marker（旧 lease）在 prepare 已被清
        # 掉，不会进这里——最后一次 attempt 不为过期结果殉葬（P1）。
        print(f"skipping claim of {execution_id}: dir holds a pending upload", flush=True)
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
    deliver_result(client, uploads, status, task, heartbeat, execution_dir, execution_id)
