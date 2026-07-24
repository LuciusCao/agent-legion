#!/usr/bin/env python3
"""Agent Legion Worker: concurrent pull supervisor for Pi/OpenClaw Agent executions."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any

from yaml import YAMLError

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from server.app.services.pi_event_compression import compress_pi_events
from server.app.workflows.pi_protocol import detect_model_error
from worker import runtime_controls
from worker.claim_manifest import apply_live_manifest
from worker.cleanup import (
    SWEEP_INTERVAL_SECONDS,
    clean_work_root,
    sweep_stale_executions,
)
from worker.host_client import Client, WorkerAuthError
from worker.registration_retry import register_from_config
from worker.status import ExecutionStatusReporter

CLAIM_BACKOFF_CAP_SECONDS = 60.0
load_claim_controls = runtime_controls.load_claim_controls


def safe_extract(archive: Path, destination: Path) -> dict[str, Any]:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.islnk() or member.issym():
                raise ValueError(f"unsafe Agent bundle member: {member.name!r}")
        tar.extractall(destination, filter="data")
    return json.loads((destination / "manifest.json").read_text(encoding="utf-8"))


def substitute(value: str, paths: dict[str, str]) -> str:
    for key, replacement in paths.items():
        value = value.replace("{" + key + "}", replacement)
    return value


def heartbeat_loop(
    client: Client,
    execution_id: str,
    lease_id: str,
    stop: threading.Event,
    interval: float,
    ownership_lost: threading.Event,
) -> None:
    """Beat until stopped; only 401/409 (ownership lost) stops the thread."""
    while not stop.wait(interval):
        try:
            status = client.heartbeat(execution_id, lease_id)
        except Exception as exc:  # transient network error: keep beating
            print(f"heartbeat error for {execution_id}: {exc}", flush=True)
            continue
        if status in (401, 409):
            print(f"heartbeat lost ownership for {execution_id}: HTTP {status}", flush=True)
            ownership_lost.set()
            return
        if status != 204:
            print(f"heartbeat unexpected status for {execution_id}: HTTP {status}", flush=True)


def terminate(proc: subprocess.Popen[bytes], grace_seconds: float) -> None:
    """Best-effort process-group SIGTERM then SIGKILL; never raises."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        print(f"Agent process {proc.pid} did not exit after SIGKILL", flush=True)


def _wait_for_exit(
    proc: subprocess.Popen[bytes],
    timeout: float,
    shutdown: threading.Event,
    shutdown_grace: float,
    ownership_lost: threading.Event,
) -> tuple[int, bool]:
    """Poll the child, reacting to shutdown/ownership loss. Returns (exit_code, report)."""
    deadline = time.monotonic() + timeout
    while True:
        if ownership_lost.is_set():
            terminate(proc, 5)
            return 1, False
        if shutdown.is_set():
            terminate(proc, shutdown_grace)
            return 130, True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            terminate(proc, 5)
            return 124, True
        try:
            return proc.wait(timeout=min(0.5, remaining)), True
        except subprocess.TimeoutExpired:
            continue


def run_execution(
    client: Client,
    claim: dict[str, Any],
    work_root: Path,
    environment: dict[str, str],
    heartbeat_interval: float,
    shutdown: threading.Event,
    shutdown_grace: float,
    status: ExecutionStatusReporter,
) -> None:
    execution_id = str(claim["execution_id"])
    lease_id = str(claim["lease_id"])
    execution_dir = work_root / execution_id
    bundle = execution_dir / "bundle.tar.gz"
    extracted = execution_dir / "bundle"
    job_dir = execution_dir / "job"
    run_dir = job_dir / "runs" / str(claim["node_key"]) / "worker"
    session_dir = run_dir / "session"
    prompt_file = run_dir / "prompt.md"
    archive = execution_dir / "result.tar.gz"
    if execution_dir.exists():
        # Stale dir from a crashed run or a re-claimed execution: drop it.
        print(f"removing stale execution dir for {execution_id}", flush=True)
        shutil.rmtree(execution_dir, ignore_errors=True)
    execution_dir.mkdir(parents=True)
    job_dir.mkdir(parents=True)
    run_dir.mkdir(parents=True)
    session_dir.mkdir(parents=True)
    status.start(
        execution_id,
        job_id=str(claim.get("job_id", "")),
        node_key=str(claim.get("node_key", "")),
        workflow_key=str(claim.get("workflow_key", "")),
        agent_id=str(claim.get("agent_id", "")),
        run_dir=str(run_dir),
    )
    stop_heartbeat = threading.Event()
    ownership_lost = threading.Event()
    heartbeat: threading.Thread | None = None
    proc: subprocess.Popen[bytes] | None = None
    report_result = True
    metadata: dict[str, Any] = {"status": "failed", "exit_code": 1}
    try:
        status.set_phase(execution_id, "downloading")
        client.download(str(claim["bundle_url"]), bundle)
        manifest = apply_live_manifest(safe_extract(bundle, extracted), claim)
        for name, ref in manifest.get("input_artifacts", {}).items():
            digest = str(ref).split(":", 1)[-1]
            target = job_dir / PurePosixPath(str(name))
            client.download(f"/api/artifacts/{digest}", target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"artifact digest mismatch: {name}")
        command_spec = manifest["command_spec"]
        prompt_file.write_text(str(command_spec["prompt"]), encoding="utf-8")
        paths = {
            "job_dir": str(job_dir),
            "skill_dir": str(extracted / "skill"),
            "session_dir": str(session_dir),
            "session_name": f"agent-legion-{execution_id}",
            "prompt_file": str(prompt_file),
        }
        command = [substitute(str(part), paths) for part in command_spec["command"]]
        heartbeat = threading.Thread(
            target=heartbeat_loop,
            args=(
                client,
                execution_id,
                lease_id,
                stop_heartbeat,
                heartbeat_interval,
                ownership_lost,
            ),
            daemon=True,
        )
        heartbeat.start()
        if shutdown.is_set():
            metadata = {
                "status": "cancelled",
                "exit_code": 130,
                "error_message": "Agent Worker is shutting down",
                "command": command,
                "output_artifacts": {},
            }
        else:
            events = run_dir / "events.jsonl"
            env = {**os.environ, **environment}
            # LLM gateway token: pi reads it via the provider's apiKey
            # "$LLM_GATEWAY_TOKEN" interpolation; keep the worker env authoritative.
            if gateway_token := os.environ.get("LLM_GATEWAY_TOKEN", ""):
                env["LLM_GATEWAY_TOKEN"] = gateway_token
            else:
                env.pop("LLM_GATEWAY_TOKEN", None)
            status.set_phase(execution_id, "running")
            with events.open("wb") as output:
                proc = subprocess.Popen(
                    command,
                    cwd=job_dir,
                    env=env,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                timeout = int(manifest.get("pi", {}).get("timeout_seconds", 600))
                exit_code, report_result = _wait_for_exit(
                    proc, timeout, shutdown, shutdown_grace, ownership_lost
                )
            if report_result:
                status.set_phase(execution_id, "uploading")
                # Scan for model errors before compression rewrites the events
                # file; then drop streaming deltas so both the local copy and
                # the uploaded archive stay small (raw events reach 100MB+).
                model_error = detect_model_error(events) if exit_code == 0 else None
                compress_pi_events(events)
                outputs: dict[str, str] = {}
                for name in manifest.get("expected_outputs", []):
                    output_path = job_dir / PurePosixPath(str(name))
                    if output_path.is_file():
                        outputs[str(name)] = client.upload_artifact(output_path)
                with tarfile.open(archive, "w:gz") as tar:
                    for name in manifest.get("expected_outputs", []):
                        output_path = job_dir / PurePosixPath(str(name))
                        if output_path.is_file():
                            tar.add(output_path, arcname=str(name))
                    tar.add(run_dir, arcname=str(run_dir.relative_to(job_dir)))
                if exit_code == 0:
                    # Pi exits 0 even when the model call fails (e.g. provider
                    # 401). The events file is worker-local and trustworthy, so
                    # scan it here and report the real failure instead of a
                    # misleading "missing outputs".
                    if model_error:
                        result_status, error = "failed", model_error
                    else:
                        result_status, error = "completed", ""
                elif shutdown.is_set():
                    result_status, error = "cancelled", "Agent Worker is shutting down"
                else:
                    result_status, error = "failed", f"Agent process exited {exit_code}"
                metadata = {
                    "status": result_status,
                    "exit_code": exit_code,
                    "error_message": error,
                    "command": command,
                    "output_artifacts": outputs,
                    # Host-side observability (DAG log view + token usage) reads
                    # events.jsonl from this dir after the archive is unpacked;
                    # it never feeds success/failure decisions.
                    "run_dir": PurePosixPath(run_dir.relative_to(job_dir)).as_posix(),
                }
    except Exception as exc:
        metadata["error_message"] = str(exc)
    finally:
        stop_heartbeat.set()
        if heartbeat is not None:
            heartbeat.join(timeout=2)
        if proc is not None and proc.poll() is None:
            terminate(proc, 5)
    try:
        if report_result:
            if not archive.exists():
                with tarfile.open(archive, "w:gz"):
                    pass
            client.report(execution_id, lease_id, metadata, archive)
    except Exception as exc:
        print(f"Agent result report failed for {execution_id}: {exc}", flush=True)
    status.finish(execution_id)
    shutil.rmtree(execution_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an Agent Legion Worker")
    parser.add_argument("--config", type=Path, default=Path("config/agent-worker.yaml"))
    args = parser.parse_args()
    config = runtime_controls.load_config(args.config)
    client = Client(str(config["host_url"]))
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    poll_interval, registration = register_from_config(client, config, stop)
    if registration is not True:
        return 2 if registration is False else 0
    max_concurrency, claim_enabled = runtime_controls.load_claim_controls(args.config)
    work_root = Path(str(config.get("work_root", "/var/lib/agent-legion-worker"))).resolve()
    clean_work_root(work_root)
    environment = {str(key): str(value) for key, value in config.get("environment", {}).items()}
    interval = float(config.get("heartbeat_interval_seconds", 15))
    shutdown_grace = float(config.get("shutdown_grace_seconds", 25))
    active: set[Future[None]] = set()
    backoff = poll_interval
    status = ExecutionStatusReporter.from_env()
    pool = ThreadPoolExecutor(
        runtime_controls.MAX_DYNAMIC_CONCURRENCY, thread_name_prefix="agent-execution"
    )
    next_sweep = time.monotonic()
    control_error: str | None = None
    try:
        while not stop.is_set():
            if time.monotonic() >= next_sweep:
                sweep_stale_executions(work_root)
                next_sweep = time.monotonic() + SWEEP_INTERVAL_SECONDS
            completed = {future for future in active if future.done()}
            active -= completed
            for future in completed:
                try:
                    future.result()
                except Exception as exc:
                    print(f"Agent execution failed: {exc}", flush=True)
            try:
                max_concurrency, claim_enabled = runtime_controls.load_claim_controls(args.config)
                control_error = None
            except (OSError, ValueError, YAMLError) as exc:
                message = str(exc)
                if message != control_error:
                    print(
                        f"Agent dynamic control reload failed; keeping previous values: {message}",
                        flush=True,
                    )
                    control_error = message
            available = max(0, max_concurrency - len(active)) if claim_enabled else 0
            claimed = False
            try:
                for _ in range(available):
                    if stop.is_set():
                        break
                    claim = client.claim(str(config["worker_id"]))
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
                        )
                    )
            except WorkerAuthError as exc:
                print(
                    f"Agent Worker rejected by server: {exc}; re-register required",
                    flush=True,
                )
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
        # shutdown_grace, so this never blocks past grace + report time.
        pool.shutdown(wait=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
