"""Worker-side execution of kind='code' claims (batch 2, protocol v2).

A code claim is self-contained: the claim response carries the manifest (with
claim-time-resolved secrets, no ``secret_config`` key) and the bundle carries
``node_code.py`` plus a ``workspace_libs`` snapshot — deliberately no
``manifest.json`` (the claim response is the only source of truth;
``server/app/agent_broker/code_dispatch.py`` builds both sides).

The node code runs inside the velites OS sandbox exactly like the Host-side
custom-code path; argv/env/read-roots/result parsing live in the shared
``shared/code_sandbox.py`` module imported by both sides (the worker image
ships worker/ + shared/, no repo checkout).

Secret boundary (VAULT-SECRET-001 extended to the Worker): the resolved
manifest lives only in memory and crosses into the child as a stdin pickle;
nothing derived from ``config`` may touch disk or logs. The Host-side
``split_manifest_config`` (server/app/agent_broker/code_dispatch.py) keeps
secret-marked keys out of the dispatch manifest before it ever reaches the
Worker.
"""

from __future__ import annotations

import hashlib
import os
import pickle
import shutil
import subprocess
import tarfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from shared.code_sandbox import (
    AUTH_FAILURE_MARKER_PATH,
    CODE_BUNDLE_NODE_FILE,
    CODE_RESULT_LOG_MEMBER,
    MAX_CONNECTION_KEY_CHARS,
    build_sandbox_argv,
    child_env,
    read_result_error,
)
from shared.material_cache import MATERIALS_CACHE_DIRNAME
from worker._atomic import atomic_write
from worker.binary_resolution import resolve_binary
from worker.bundle_io import download_input_artifacts, safe_extract_tree
from worker.material_fetch import materialize_claim_material
from worker.process_lifecycle import AGENT_PGID_FILENAME, terminate, wait_for_exit
from worker.execution.pending import refuse_if_pending_upload
from worker.upload.queue import UploadTask

if TYPE_CHECKING:
    from worker.execution.heartbeat import ExecutionHeartbeat
    from worker.host.client import Client
    from worker.status import ExecutionStatusReporter

# Where the sandboxed child writes its JSON result (sibling of the Host-side
# .custom_node_result.json; deliberately a different filename so the two
# paths cannot collide in shared tooling).
RESULT_BASENAME = ".code_result.json"

_CANCEL_EVENTS: dict[str, threading.Event] = {}
_CANCEL_LOCK = threading.Lock()


def register_cancellation(execution_id: str) -> threading.Event:
    """Register a cancel event for one in-flight code execution."""
    event = threading.Event()
    with _CANCEL_LOCK:
        _CANCEL_EVENTS[execution_id] = event
    return event


def unregister_cancellation(execution_id: str) -> None:
    with _CANCEL_LOCK:
        _CANCEL_EVENTS.pop(execution_id, None)


def cancel_executions(execution_ids: list[str]) -> int:
    """Cancel callback for heartbeat bodies (batch 2 decision 6).

    The Host lists this Worker's claimed kind='code' executions whose job was
    paused/cancelled; matching local runs get their event set and the wait
    loop kills the process group (SIGTERM semantics unchanged)."""
    matched = []
    with _CANCEL_LOCK:
        for execution_id in execution_ids:
            event = _CANCEL_EVENTS.get(str(execution_id))
            if event is not None:
                event.set()
                matched.append(str(execution_id))
    if matched:
        print(f"Host cancelled code execution(s): {', '.join(matched)}", flush=True)
    return len(matched)


@dataclass(frozen=True)
class PreparedCode:
    manifest: dict[str, Any]
    code_text: str
    libs_root: Path


def prepare_code_execution(
    client: Client,
    claim: dict[str, Any],
    execution_dir: Path,
    download_slots: threading.Semaphore,
) -> PreparedCode:
    """Download/verify the code bundle and stage input artifacts.

    The claim-response manifest is authoritative (the bundle has none); the
    extracted ``node_code.py`` must hash to the manifest's ``code_hash``
    (sha256 of the utf-8 code text) or the run is refused."""
    execution_id = str(claim["execution_id"])
    manifest = claim["manifest"]
    if not isinstance(manifest, dict):
        raise ValueError("code claim is missing its manifest")
    bundle = execution_dir / "bundle.tar.gz"
    extracted = execution_dir / "bundle"
    job_dir = execution_dir / "job"
    if execution_dir.exists():
        # #203：marker 归属本 claim 的 lease 才拒绝（排队中的未投递结果）；
        # 旧 lease 的孤儿 marker（report 必 409）随 stale 目录一起清掉。
        refuse_if_pending_upload(execution_dir, claim)
        # Stale dir from a crashed run or a re-claimed execution: drop it.
        print(f"removing stale execution dir for {execution_id}", flush=True)
        shutil.rmtree(execution_dir, ignore_errors=True)
    execution_dir.mkdir(parents=True)
    job_dir.mkdir(parents=True)
    with download_slots:
        client.download(str(claim["bundle_url"]), bundle)
    safe_extract_tree(bundle, extracted)
    code_path = extracted / CODE_BUNDLE_NODE_FILE
    if not code_path.is_file():
        raise ValueError(f"code bundle is missing {CODE_BUNDLE_NODE_FILE}")
    code_text = code_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(code_text.encode("utf-8")).hexdigest()
    expected = str(manifest.get("code_hash") or "")
    if not expected or digest != expected:
        raise ValueError(
            f"code_hash mismatch for capability {manifest.get('capability')!r}:"
            f" manifest {expected or '<missing>'}, bundle {digest}; refusing to run"
        )
    download_input_artifacts(client, manifest, job_dir, download_slots)
    return PreparedCode(manifest=manifest, code_text=code_text, libs_root=extracted)


def build_child_payload(
    manifest: dict[str, Any],
    code_text: str,
    job_dir: Path,
    *,
    materials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stdin pickle payload for ``workspace_libs.code_child`` (memory only).

    Rebuilds the runtime dict the Host-side ``build_runtime``
    (server/app/executors/_code_runtime.py) hands to node code, sourced from
    the manifest's prefetched ``runtime_context`` — the child never gets a
    database handle. ``node_config`` carries resolved secrets; that is why
    the payload rides stdin and is never written to disk. ``materials`` is
    the local materialization block for a material-input job (design §6.2),
    mirroring the Host-side ``runtime["materials"]``."""
    context = manifest.get("runtime_context")
    context = dict(context) if isinstance(context, Mapping) else {}
    node_config = manifest.get("config")
    job = context.get("job")
    runtime: dict[str, Any] = {
        "job_dir": str(job_dir),
        "log_path": str(manifest.get("log_path") or ""),
        "inputs": list(manifest.get("inputs") or []),
        "expected_outputs": list(manifest.get("expected_outputs") or []),
        "capability": str(manifest.get("capability") or ""),
        "node_key": str(manifest.get("node_key") or ""),
        "workflow_key": str(manifest.get("workflow_key") or ""),
        "execution_id": str(manifest.get("execution_id") or ""),
        "workspace_id": str(manifest.get("workspace_id") or ""),
        "workspace": dict(context.get("workspace") or {}),
        "job": dict(job) if isinstance(job, Mapping) else {},
        "settings_config": dict(context.get("settings_config") or {}),
        "node_config": dict(node_config) if isinstance(node_config, Mapping) else {},
        "skill_versions": dict(context.get("skill_versions") or {}),
    }
    if context.get("job_batch") is not None:
        runtime["job_batch"] = context["job_batch"]
    if materials is not None:
        runtime["materials"] = materials
    return {
        "code": code_text,
        "job": runtime["job"],
        "job_dir": str(job_dir),
        "runtime": runtime,
    }


def read_auth_failure_key(job_dir: Path, manifest: dict[str, Any]) -> str:
    """Read the connection key a node recorded via ``report_auth_failure``.

    Counterpart of ``_code_runtime.py::consume_auth_failure_marker`` (keep in
    sync): the Host-side parent invalidates the cached token directly; the
    Worker reports the key in the result metadata and the Host invalidates
    after commit (batch 2 decision 7). Falls back to the config's connection
    selector when the marker is empty, same as the Host."""
    key = ""
    marker = job_dir / AUTH_FAILURE_MARKER_PATH
    try:
        if marker.is_file():
            key = marker.read_text(encoding="utf-8").strip()
    except OSError:
        key = ""
    if not key:
        config = manifest.get("config")
        if isinstance(config, Mapping):
            key = str(config.get("connection") or "").strip()
    return key[:MAX_CONNECTION_KEY_CHARS]


def _outcome(
    exit_code: int,
    result_path: Path,
    job_dir: Path,
    expected_outputs: tuple[str, ...],
    timeout: float,
    write_error: list[BaseException],
) -> dict[str, str]:
    """Map (exit code, result file, outputs) to the reported status/error."""
    if exit_code == 130:
        # Shutdown or Host-driven cancel: wait_for_exit SIGTERMs the group and
        # reports 130 in both cases (same convention as the agent path).
        return {"status": "cancelled", "error_message": "execution was cancelled"}
    if exit_code == 124:
        return {
            "status": "failed",
            "error_message": f"code node timed out after {timeout:g}s (sandboxed)",
        }
    if write_error:
        return {
            "status": "failed",
            "error_message": (
                f"code child exited before reading its payload ({type(write_error[0]).__name__})"
            ),
        }
    result_error = read_result_error(result_path)
    if result_error is not None:
        return {"status": "failed", "error_message": result_error}
    missing = [name for name in expected_outputs if not (job_dir / PurePosixPath(name)).is_file()]
    if missing:
        # Same verdict as the Host-side CodeExecutor._check_outputs.
        return {
            "status": "failed",
            "error_message": f"Missing outputs after code run: {', '.join(missing)}",
        }
    return {"status": "completed", "error_message": ""}


def _feed_stdin(
    proc: subprocess.Popen[bytes], payload: bytes, write_error: list[BaseException]
) -> None:
    """Feed the payload from a daemon thread so a slow-starting child cannot
    stall the deadline/cancellation loop on a full pipe buffer."""
    try:
        assert proc.stdin is not None
        proc.stdin.write(payload)
        proc.stdin.close()
    except (BrokenPipeError, OSError) as exc:
        write_error.append(exc)


def execute_code(
    client: Client,
    claim: dict[str, Any],
    execution_dir: Path,
    status_fields: dict[str, str],
    download_slots: threading.Semaphore,
    shutdown: threading.Event,
    shutdown_grace: float,
    ownership_lost: threading.Event,
    heartbeat: ExecutionHeartbeat,
    status: ExecutionStatusReporter,
) -> UploadTask | None:
    """Run one kind='code' claim sandboxed; None = lease lost (Host owns it).

    Called from ``worker.execution.run.run_execution``'s kind branch, which
    owns the shared post-exit tail (release-slot, heartbeat adoption, upload)."""
    execution_id = str(claim["execution_id"])
    job_dir = execution_dir / "job"
    cancel_event = register_cancellation(execution_id)
    proc: subprocess.Popen[bytes] | None = None
    try:
        prepared = prepare_code_execution(client, claim, execution_dir, download_slots)
        manifest = prepared.manifest
        velites = resolve_binary("velites")
        if velites is None:
            # Startup preflight normally catches this; the bundled copy or PATH
            # can still change under a long-running Worker — fail closed
            # (EXEC-CODE-003).
            raise RuntimeError(
                "code execution requires the velites binary (velites sandbox wrap) "
                "in data/bin or on PATH; refusing to run unsandboxed"
            )
        result_path = job_dir / RESULT_BASENAME
        # A leftover result/marker from a previous attempt must never fake a
        # fresh outcome (same hygiene as the Host-side sandbox path).
        result_path.unlink(missing_ok=True)
        (job_dir / AUTH_FAILURE_MARKER_PATH).unlink(missing_ok=True)
        log_path = execution_dir / CODE_RESULT_LOG_MEMBER
        timeout = float(manifest.get("timeout_seconds") or 1800)
        # Material input (design §6.2): materialize into the work-root cache
        # before spawning — the sandboxed child only ever reads the local
        # path through the static allow-read grant (MATERIAL-ACCESS-001).
        materials = materialize_claim_material(manifest, execution_dir)
        command = build_sandbox_argv(
            velites,
            job_dir,
            [prepared.libs_root],
            result_path,
            sandbox_network=manifest.get("sandbox_network"),
            materials_cache_root=execution_dir.parent / MATERIALS_CACHE_DIRNAME,
            marker=f"agent-legion-{execution_id}",
        )
        payload = pickle.dumps(
            build_child_payload(manifest, prepared.code_text, job_dir, materials=materials)
        )
        status.set_phase(execution_id, "running")
        log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=log_fd,
                stderr=subprocess.STDOUT,
                cwd=str(job_dir),
                env=child_env(prepared.libs_root),
                # velites does not forward signals: the process group is the
                # kill unit so sandbox-exec grandchildren are not orphaned.
                start_new_session=True,
            )
        finally:
            os.close(log_fd)
        # executor 被 SIGKILL 时 supervisor 经 orphan_reaper 兜底回收：argv 尾部
        # 的 agent-legion-<execution_id> 标记让 reaper 能把进程组归属到本执行
        # （同 agent 路径的 --name 注入，#186）。
        atomic_write(execution_dir / AGENT_PGID_FILENAME, str(proc.pid))
        heartbeat.proc_ref["proc"] = proc
        write_error: list[BaseException] = []
        feeder = threading.Thread(
            target=_feed_stdin, args=(proc, payload, write_error), daemon=True
        )
        feeder.start()
        exit_code, report = wait_for_exit(
            proc, timeout, shutdown, shutdown_grace, ownership_lost, cancel_event
        )
        feeder.join(timeout=1)
        if not report:
            return None
        outcome = _outcome(
            exit_code,
            result_path,
            job_dir,
            tuple(str(name) for name in manifest.get("expected_outputs", [])),
            timeout,
            write_error,
        )
        outcome["auth_failure_connection"] = read_auth_failure_key(job_dir, manifest)
        return UploadTask(
            execution_id=execution_id,
            lease_id=str(claim["lease_id"]),
            execution_dir=execution_dir,
            node_key=str(claim["node_key"]),
            status_fields=status_fields,
            kind="process",
            exec_kind="code",
            exit_code=exit_code,
            expected_outputs=tuple(str(name) for name in manifest.get("expected_outputs", [])),
            command=tuple(command),
            code_result=outcome,
            # #160 D12：直传 S3 的上传规格（空 = 旧 CAS 通道）。
            artifact_uploads=dict(manifest.get("artifact_uploads") or {}),
        )
    finally:
        if proc is not None and proc.poll() is None:
            terminate(proc, 5)
        unregister_cancellation(execution_id)


def prepare_code_result(task: UploadTask) -> tuple[dict[str, Any], Path, list[str]]:
    """Build (metadata, archive, output names) for a kind='code' result.

    Archive contract (mirrors the Host-side reader
    ``server/app/agent_broker/result_unpack.py`` — keep in sync): expected
    outputs at their job-dir-relative names plus ``node.log`` at the archive
    root; no events.jsonl/run_dir. The captured node.log ships even for
    cancelled runs (batch 2 decision 10)."""
    archive = task.execution_dir / "result.tar.gz"
    job_dir = task.execution_dir / "job"
    outcome = task.code_result or {}
    outputs = [name for name in task.expected_outputs if (job_dir / PurePosixPath(name)).is_file()]
    metadata: dict[str, Any] = {
        "status": str(outcome.get("status") or "failed"),
        "exit_code": task.exit_code,
        "error_message": str(outcome.get("error_message") or ""),
        "command": list(task.command),
        "output_artifacts": {},
    }
    auth_failure = str(outcome.get("auth_failure_connection") or "").strip()
    if auth_failure:
        metadata["auth_failure_connection"] = auth_failure
    # #160 D12：直传判定与 upload_queue._bulk_transfer 一致（#201 收敛进
    # UploadTask.is_direct_upload）；直传时产物不内嵌归档（字节走 presigned
    # PUT），node.log 照常携带。
    direct = task.is_direct_upload(outputs)
    with tarfile.open(archive, "w:gz") as tar:
        if not direct:
            for name in outputs:
                tar.add(job_dir / PurePosixPath(name), arcname=name)
        node_log = task.execution_dir / CODE_RESULT_LOG_MEMBER
        if node_log.is_file():
            tar.add(node_log, arcname=CODE_RESULT_LOG_MEMBER)
    return metadata, archive, outputs
