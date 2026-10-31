"""Native Ubuntu N0 vertical spike for Host/Worker sandboxes (test/CI only).

Run only through native_worker_spike.service. No Docker Host/Worker, privilege
escalation, production protocol changes, or unsandboxed fallback is involved.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import requests
import yaml
from cryptography.fernet import Fernet

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.e2e._database import db_dsn, reset_database  # noqa: E402
from scripts.e2e.native_worker_spike_agent import enqueue_agent_job  # noqa: E402
from scripts.e2e.native_worker_spike_control import resume_workspace  # noqa: E402
from scripts.e2e.native_worker_spike_payload import (  # noqa: E402
    CLAIM_SECRET,
    PARENT_SECRET_ENV,
    check_failures,
)
from scripts.e2e.native_worker_spike_stub import AgentGateway, ProbeListener  # noqa: E402
from server.app.agent_broker import AgentExecutionBroker  # noqa: E402
from server.app.agent_broker.code_dispatch import CodeDispatchService  # noqa: E402
from server.app.agent_control.register_tokens import AgentRegisterTokenStore  # noqa: E402
from server.app.executors.code import CodeExecutor  # noqa: E402
from server.app.executors.contracts import CodeCapabilityConfig  # noqa: E402
from server.app.executors.models import ExecutionContext  # noqa: E402
from server.app.jobs import JobQueries  # noqa: E402
from server.app.services.artifact_store import ArtifactStore  # noqa: E402
from server.app.services.vault import VaultService  # noqa: E402
from server.app.settings import Settings  # noqa: E402
from server.app.workflows.schema import WorkflowNode  # noqa: E402
from shared.code_sandbox import read_roots  # noqa: E402

WORKSPACE_ID = "native-spike"
DATABASE_NAME = "agent_legion_native_spike"
PARENT_SECRET_VALUE = "native-spike-parent-canary-secret"


def _namespace(name: str) -> str:
    return os.readlink(Path("/proc/self/ns") / name)


def _verify_n0(state_dir: Path, output_dir: Path) -> dict[str, Any]:
    release = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            release[key] = value.strip('"')
    if (release.get("ID"), release.get("VERSION_ID")) != ("ubuntu", "24.04"):
        raise RuntimeError("native spike requires a stock Ubuntu 24.04 runner")
    status = {}
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            status[key] = value.strip()
    capability_names = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
    if os.getuid() == 0 or os.geteuid() == 0:
        raise RuntimeError("N0 must run as a fixed non-root system account")
    if os.environ.get("USER") != "agent-legion-spike":
        raise RuntimeError("N0 fixed service account is missing")
    if Path(os.environ.get("HOME", "")).resolve() != state_dir.resolve():
        raise RuntimeError("N0 requires its independent service HOME")
    if status.get("NoNewPrivs") != "1" or any(
        int(status.get(name, "1"), 16) != 0 for name in capability_names
    ):
        raise RuntimeError("N0 requires NoNewPrivileges=yes and empty capabilities")
    document = {
        "pid": os.getpid(),
        "uid": os.getuid(),
        "home": str(state_dir),
        "cgroup": Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines(),
        "no_new_privileges": status["NoNewPrivs"],
        "capabilities": {name: status.get(name) for name in capability_names},
        "namespaces": {name: _namespace(name) for name in ("user", "mnt", "pid", "net")},
    }
    (output_dir / "n0-ready.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return document


def _probe_config(
    *,
    case: str,
    listener: ProbeListener,
    canary: Path,
    parent: dict[str, Any],
    allow_network: bool,
    output_name: str = "evidence.json",
    expect_pid_one: bool = True,
) -> dict[str, Any]:
    return {
        "case": case,
        "listener_host": listener.host,
        "listener_port": listener.port,
        "canary_path": str(canary),
        "parent_namespaces": parent["namespaces"],
        "parent_pid": parent["pid"],
        "allow_network": allow_network,
        "output_name": output_name,
        "expect_pid_one": expect_pid_one,
    }


def _assert_evidence(
    path: Path,
    *,
    case: str,
    listener: ProbeListener,
    before: int,
    allow_network: bool,
    verify_listener: bool = True,
) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"{case}: payload did not execute; {path.name} is missing")
    document = json.loads(path.read_text(encoding="utf-8"))
    failures = check_failures(document)
    if document.get("case") != case or failures:
        raise RuntimeError(f"{case}: invalid evidence; failed checks: {failures}")
    expected_connections = before + (1 if allow_network else 0)
    if verify_listener and listener.count() != expected_connections:
        raise RuntimeError(f"{case}: listener count {listener.count()} != {expected_connections}")
    return document


def _child_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ}
    env["PATH"] = f"{REPO_ROOT / 'data' / 'bin'}:{env.get('PATH', '')}"
    return env


def _run_direct(
    case: str,
    job_dir: Path,
    config: dict[str, Any],
    listener: ProbeListener,
    output_dir: Path,
) -> dict[str, Any]:
    job_dir.mkdir(parents=True)
    config_path = job_dir / "probe-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    command = [
        str(REPO_ROOT / "data" / "bin" / "velites"),
        "sandbox",
        "wrap",
        "--cwd",
        str(job_dir),
    ]
    for root in read_roots([REPO_ROOT / "scripts" / "e2e"]):
        command.extend(["--allow-read", root])
    if config["allow_network"]:
        command.append("--allow-network")
    command.extend(
        [
            "--",
            str(Path(sys.executable).resolve()),
            str(REPO_ROOT / "scripts" / "e2e" / "native_worker_spike_payload.py"),
            "--config",
            str(config_path),
        ]
    )
    before = listener.count()
    completed = subprocess.run(
        command,
        cwd=job_dir,
        env=_child_env(),
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    (output_dir / f"{case}.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"{case}: direct wrap exited {completed.returncode}")
    return _assert_evidence(
        job_dir / config["output_name"],
        case=case,
        listener=listener,
        before=before,
        allow_network=config["allow_network"],
    )


def _run_host_code(
    case: str,
    job_dir: Path,
    log_path: Path,
    config: dict[str, Any],
    listener: ProbeListener,
) -> dict[str, Any]:
    context = ExecutionContext(
        execution_id=f"exec-{case}",
        lease_id=f"lease-{case}",
        node_run_id=1,
        executor_id="code",
        workspace_id=WORKSPACE_ID,
        job_id=f"job-{case}",
        workflow_key=WORKSPACE_ID,
        node_key="probe",
        capability="native_probe",
        workspace={"id": WORKSPACE_ID},
        job={"id": f"job-{case}"},
        job_dir=job_dir,
        log_path=log_path,
        inputs=(),
        expected_outputs=(config["output_name"],),
        node_config={
            "probe": config,
            "claim_secret": CLAIM_SECRET,
            "sandbox_network": config["allow_network"],
            "timeout_seconds": 45,
        },
        node_code=(REPO_ROOT / "scripts" / "e2e" / "native_worker_spike_payload.py").read_text(
            encoding="utf-8"
        ),
    )
    before = listener.count()
    result = CodeExecutor(REPO_ROOT, materials_cache_root=job_dir.parent / "materials").execute(
        context
    )
    if result.status != "completed":
        raise RuntimeError(f"{case}: Host code child failed: {result.error_message}")
    return _assert_evidence(
        job_dir / config["output_name"],
        case=case,
        listener=listener,
        before=before,
        allow_network=config["allow_network"],
    )


def _settings(dsn: str, data_dir: Path) -> Settings:
    return Settings(
        root_dir=REPO_ROOT,
        data_dir=data_dir,
        videos_dir=data_dir / "videos",
        logs_dir=data_dir / "logs",
        packages_dir=data_dir / "packages",
        jobs_dir=data_dir / "jobs",
        config={},
        database_url=dsn,
    )


def _insert_job(job_db: JobQueries, job_id: str, node_key: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'Native spike') on conflict(id) do nothing",
            (WORKSPACE_ID,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id) values (%s, %s, 'question', %s)",
            (job_id, WORKSPACE_ID, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, %s)", (job_id, node_key))
        conn.execute(
            "insert into workspace_agent_capacities(workspace_id, max_concurrency) values (%s, 4)"
            " on conflict(workspace_id) do nothing",
            (WORKSPACE_ID,),
        )


def _enqueue_worker_jobs(
    *,
    dsn: str,
    settings: Settings,
    code_config: dict[str, Any],
    code_allow_config: dict[str, Any],
    agent_config: dict[str, Any],
) -> None:
    job_db = JobQueries(dsn, jobs_dir=settings.jobs_dir)
    _insert_job(job_db, "worker-agent", "agent")
    broker = AgentExecutionBroker(
        dsn, bundle_dir=settings.data_dir / "agent_bundles", data_dir=settings.data_dir
    )
    store = ArtifactStore(settings.data_dir / "artifacts", dsn)
    VaultService(dsn, {}).set(WORKSPACE_ID, "native-spike", CLAIM_SECRET)
    code_text = (REPO_ROOT / "scripts" / "e2e" / "native_worker_spike_payload.py").read_text(
        encoding="utf-8"
    )
    for job_id, config, allow_network in (
        ("worker-code", code_config, False),
        ("worker-code-allow", code_allow_config, True),
    ):
        _insert_job(job_db, job_id, "probe")
        code_job_dir = settings.jobs_dir / job_id
        code_job_dir.mkdir(parents=True, exist_ok=True)
        node = WorkflowNode(
            key="probe", label="probe", capability="native_probe", outputs=["evidence.json"]
        )
        enqueued = CodeDispatchService(settings, broker, store, job_db).enqueue(
            capability="native_probe",
            capability_config=CodeCapabilityConfig(
                timeout_seconds=60,
                sandbox_network=allow_network,
                config_schema={
                    "properties": {
                        "probe": {"type": "string"},
                        "claim_secret": {"type": "string", "secret": True},
                    }
                },
            ),
            workspace={"id": WORKSPACE_ID},
            job={"id": job_id},
            workflow_key=WORKSPACE_ID,
            node=node,
            job_dir=code_job_dir,
            log_path=settings.logs_dir / f"{job_id}.log",
            inputs=(),
            code_text=code_text,
            custom_code=True,
            config={"probe": json.dumps(config)},
            secret_config={"claim_secret": {"secret_ref": "native-spike"}},
        )
        if not enqueued:
            raise RuntimeError(f"failed to enqueue real Worker code request {job_id}")

    enqueue_agent_job(
        settings=settings,
        agent_config=agent_config,
        broker=broker,
        store=store,
    )


def _wait_http(url: str, process: subprocess.Popen[bytes], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"process exited {process.returncode} before {url} became ready")
        try:
            if requests.get(url, timeout=1).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {url}")


def _start_process(
    command: list[str], env: dict[str, str], log_path: Path
) -> subprocess.Popen[bytes]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("wb")
    try:
        return subprocess.Popen(
            command, cwd=REPO_ROOT, env=env, stdout=log, stderr=log, start_new_session=True
        )
    finally:
        log.close()


def _stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def _worker_config(
    *, config_dir: Path, host_url: str, token: str, work_root: Path, models_path: Path
) -> Path:
    token_dir = config_dir / "register_tokens"
    token_dir.mkdir(parents=True)
    token_id = token.partition(".")[0]
    token_path = token_dir / f"{token_id}.token"
    token_path.write_text(token, encoding="utf-8")
    token_path.chmod(0o600)
    config = {
        "host_url": host_url,
        "worker_id": "native-worker",
        "name": "Native Ubuntu spike Worker",
        "runtimes": ["velites"],
        "max_concurrency": 1,
        "max_code_concurrency": 1,
        "claim_enabled": True,
        "work_root": str(work_root),
        "poll_interval_seconds": 0.2,
        "heartbeat_interval_seconds": 2,
        "shutdown_grace_seconds": 5,
        "upload_max_concurrency": 1,
        "environment": {"VELITES_MODELS_PATH": str(models_path)},
    }
    path = config_dir / "worker.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def _wait_worker_results(
    job_db: JobQueries, process: subprocess.Popen[bytes]
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"real Worker exited unexpectedly: {process.returncode}")
        with job_db.connect() as conn:
            rows = conn.execute(
                "select kind, state, worker_id, outcome_json from agent_execution_requests"
                " where job_id in ('worker-code', 'worker-code-allow', 'worker-agent')"
                " order by kind, job_id"
            ).fetchall()
        if len(rows) == 3 and all(row["state"] == "done" for row in rows):
            return [dict(row) for row in rows]
        time.sleep(0.2)
    raise RuntimeError("timed out waiting for real Worker claim/results")


def _verify_agent_events(path: Path, output_dir: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if CLAIM_SECRET in raw or PARENT_SECRET_VALUE in raw:
        raise RuntimeError("secret leaked into agent events")
    shutil.copyfile(path, output_dir / "agent-events.jsonl")
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    types = [event["type"] for event in events]
    if types[0] != "session" or types[-2:] != ["outputs_validation", "agent_end"]:
        raise RuntimeError(f"invalid agent event boundaries: {types}")
    if "message_update" in types or "tool_execution_update" in types:
        raise RuntimeError("delta events are forbidden")
    ends = [event for event in events if event["type"] == "tool_execution_end"]
    if [event["toolName"] for event in ends] != ["bash", "write"] or any(
        event["isError"] for event in ends
    ):
        raise RuntimeError("agent bash/write tool calls did not complete successfully")
    return {"event_types": types, "tool_names": [event["toolName"] for event in ends]}


def _run_worker_chain(
    *,
    dsn: str,
    state_dir: Path,
    output_dir: Path,
    code_config: dict[str, Any],
    code_allow_config: dict[str, Any],
    agent_config: dict[str, Any],
    listener: ProbeListener,
    gateway: AgentGateway,
) -> dict[str, Any]:
    data_dir = state_dir / "host-data"
    settings = _settings(dsn, data_dir)
    for path in (data_dir, settings.logs_dir, settings.jobs_dir):
        path.mkdir(parents=True, exist_ok=True)
    _enqueue_worker_jobs(
        dsn=dsn,
        settings=settings,
        code_config=code_config,
        code_allow_config=code_allow_config,
        agent_config=agent_config,
    )
    models_path = state_dir / "models.json"
    models_path.write_text(
        json.dumps(
            {
                "providers": {
                    "gateway": {
                        "api": "openai-completions",
                        "baseUrl": gateway.base_url,
                        "apiKey": "native-stub-key",
                        "models": ["stub-model"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    models_path.chmod(0o600)
    host_env = _child_env()
    host_env.update(
        {
            "AGENT_LEGION_SKIP_DOTENV": "1",
            "AGENT_LEGION_DATABASE_URL": dsn,
            "AGENT_LEGION_DATA_DIR": str(data_dir),
            "AGENT_LEGION_VAULT_MASTER_KEY": os.environ["AGENT_LEGION_VAULT_MASTER_KEY"],
        }
    )
    port_socket = __import__("socket").socket()
    port_socket.bind(("127.0.0.1", 0))
    port = port_socket.getsockname()[1]
    port_socket.close()
    host_url = f"http://127.0.0.1:{port}"
    host = _start_process(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "server.app.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        host_env,
        output_dir / "backend.log",
    )
    worker = None
    try:
        _wait_http(f"{host_url}/api/health", host, 60)
        with requests.Session() as control:
            resume_workspace(control, host_url, WORKSPACE_ID)
        _token_id, token = AgentRegisterTokenStore(dsn).issue_register_token(
            workspace_id=WORKSPACE_ID, label="native-spike"
        )
        config = _worker_config(
            config_dir=state_dir / "worker-config",
            host_url=host_url,
            token=token,
            work_root=state_dir / "worker-work",
            models_path=models_path,
        )
        worker_env = _child_env()
        worker_env["AGENT_WORKER_EXPECT_RUNTIMES"] = "velites"
        worker_before = listener.count()
        worker = _start_process(
            [sys.executable, "worker/executor.py", "--config", str(config)],
            worker_env,
            output_dir / "worker.log",
        )
        rows = _wait_worker_results(JobQueries(dsn, jobs_dir=settings.jobs_dir), worker)
        for row in rows:
            serialized = json.dumps(row)
            if CLAIM_SECRET in serialized or PARENT_SECRET_VALUE in serialized:
                raise RuntimeError("secret leaked into Worker result metadata")
            outcome = json.loads(row["outcome_json"])
            if row["worker_id"] != "native-worker" or outcome.get("status") != "completed":
                raise RuntimeError(f"Worker result was not a successful real claim: {row}")
        code_evidence = _assert_evidence(
            settings.jobs_dir / "worker-code" / "evidence.json",
            case="worker-code-no-network",
            listener=listener,
            before=worker_before,
            allow_network=False,
            verify_listener=False,
        )
        code_allow_evidence = _assert_evidence(
            settings.jobs_dir / "worker-code-allow" / "evidence.json",
            case="worker-code-allow-network",
            listener=listener,
            before=worker_before,
            allow_network=True,
            verify_listener=False,
        )
        agent_evidence = _assert_evidence(
            settings.jobs_dir / "worker-agent" / "agent_bash_evidence.json",
            case="worker-agent-bash",
            listener=listener,
            before=worker_before,
            allow_network=True,
            verify_listener=False,
        )
        if listener.count() != worker_before + 2:
            raise RuntimeError("real Worker probes must add exactly two listener connections")
        events = _verify_agent_events(
            settings.jobs_dir / "worker-agent" / "runs" / "agent" / "worker" / "events.jsonl",
            output_dir,
        )
        return {
            "rows": rows,
            "code": code_evidence,
            "code_allow": code_allow_evidence,
            "agent": agent_evidence,
            "events": events,
        }
    finally:
        _stop_process(worker)
        _stop_process(host)


def _preserve_payload_evidence(state_dir: Path, output_dir: Path) -> None:
    destination = output_dir / "payload-evidence"
    for root in (state_dir / "work", state_dir / "host-data" / "jobs"):
        if not root.is_dir():
            continue
        for name in ("evidence.json", "agent_bash_evidence.json"):
            for path in root.rglob(name):
                relative = path.relative_to(state_dir)
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    state_dir = args.state_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    listener = ProbeListener()
    gateway = AgentGateway("agent_result.json")
    canary_fd = -1
    try:
        parent = _verify_n0(state_dir, output_dir)
        canary = state_dir / "canary" / "secret.txt"
        canary.parent.mkdir(parents=True, exist_ok=True)
        canary.write_text(PARENT_SECRET_VALUE, encoding="utf-8")
        canary.chmod(0o600)
        canary_fd = os.open(canary, os.O_RDONLY)
        os.environ[PARENT_SECRET_ENV] = PARENT_SECRET_VALUE
        os.environ["AGENT_LEGION_VAULT_MASTER_KEY"] = Fernet.generate_key().decode()
        os.environ["AGENT_LEGION_SKIP_DOTENV"] = "1"
        reset_database(DATABASE_NAME)
        dsn = db_dsn(DATABASE_NAME)
        results: dict[str, Any] = {"n0": parent}
        for allow in (False, True):
            case = f"direct-{'allow' if allow else 'no'}-network"
            config = _probe_config(
                case=case,
                listener=listener,
                canary=canary,
                parent=parent,
                allow_network=allow,
            )
            results[case] = _run_direct(
                case, state_dir / "work" / case, config, listener, output_dir
            )
        for allow in (False, True):
            case = f"host-code-{'allow' if allow else 'no'}-network"
            config = _probe_config(
                case=case,
                listener=listener,
                canary=canary,
                parent=parent,
                allow_network=allow,
            )
            results[case] = _run_host_code(
                case,
                state_dir / "work" / case / "job",
                state_dir / "logs" / f"{case}.log",
                config,
                listener,
            )
        worker_code = _probe_config(
            case="worker-code-no-network",
            listener=listener,
            canary=canary,
            parent=parent,
            allow_network=False,
        )
        worker_code_allow = _probe_config(
            case="worker-code-allow-network",
            listener=listener,
            canary=canary,
            parent=parent,
            allow_network=True,
        )
        worker_agent = _probe_config(
            case="worker-agent-bash",
            listener=listener,
            canary=canary,
            parent=parent,
            allow_network=True,
            output_name="agent_bash_evidence.json",
            expect_pid_one=False,
        )
        results["worker"] = _run_worker_chain(
            dsn=dsn,
            state_dir=state_dir,
            output_dir=output_dir,
            code_config=worker_code,
            code_allow_config=worker_code_allow,
            agent_config=worker_agent,
            listener=listener,
            gateway=gateway,
        )
        if canary.read_text(encoding="utf-8") != PARENT_SECRET_VALUE:
            raise RuntimeError("sandbox canary was modified")
        summary = {
            "schema_version": 1,
            "success": True,
            "listener_connections": listener.count(),
            "cases": results,
        }
        (output_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return 0
    except Exception as exc:
        failure = {
            "schema_version": 1,
            "success": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        (output_dir / "failure.json").write_text(
            json.dumps(failure, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        traceback.print_exc()
        return 1
    finally:
        _preserve_payload_evidence(state_dir, output_dir)
        if canary_fd >= 0:
            os.close(canary_fd)
        gateway.close()
        listener.close()


if __name__ == "__main__":
    raise SystemExit(main())
