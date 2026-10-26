"""Drive real Host/Worker register -> sandbox -> upload -> restart measurement runs.

Run on native Linux with Docker and the project Python environment. The only
LLM is a deterministic loopback stub; jobs, remote code claims, velites agent
execution, artifact uploads, and control-plane transitions are all real.
"""

from __future__ import annotations

import argparse
import http.server
import json
import logging
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import requests
import yaml
from cryptography.fernet import Fernet

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.e2e._llm_stub import _sse  # noqa: E402
from scripts.e2e._main_flow_seed import DRAFT_OUTPUT, SKILL_KEY, _ensure_skill_repo  # noqa: E402
from scripts.stress._stress_auth import ensure_admin_session  # noqa: E402
from scripts.stress.worker_resource_desktop import cgroup_reader  # noqa: E402
from scripts.stress.worker_resource_evidence import (  # noqa: E402
    ensure_measurement_bucket,
    measure_upload_backlog,
    write_iteration_metadata,
    write_sanitized_logs,
)
from scripts.stress.worker_resource_identity import (  # noqa: E402
    ContractViolationError,
    ExecutionReadinessError,
    verify_remote_uploads,
)
from scripts.stress.worker_resource_measurement import (  # noqa: E402
    CgroupSampler,
    sanitize_log_text,
)
from server.app.agent_control.register_tokens import AgentRegisterTokenStore  # noqa: E402
from server.app.db.transaction import write_transaction  # noqa: E402
from server.app.jobs import JobQueries  # noqa: E402
from server.app.services.node_code_seeding import seed_workspace_node_code  # noqa: E402
from server.app.services.node_codes import NodeCodeService  # noqa: E402
from server.app.services.workflow_revisions import WorkflowRevisionService  # noqa: E402
from server.app.storage import S3Settings, S3StorageClient  # noqa: E402
from server.app.worker_control import WorkspaceWorkerControl  # noqa: E402
from server.app.workflows.loader import workflow_definition_from_dict  # noqa: E402

LOGGER = logging.getLogger("worker-resource-measurement")
COMPOSE_FILE = PROJECT_ROOT / "deploy" / "compose.worker.measurement.yaml"
WORKER_ID = "resource-measurement-worker"
WORKSPACES = {
    "agent": "resource_agent",
    "code": "resource_code",
    "mixed": "resource_mixed",
    "upload-backlog": "resource_upload",
}
SCENARIOS = (
    "agent",
    "code",
    "mixed",
    "upload-backlog",
    "pending-restart",
    "executor-restart",
    "container-restart",
)


class DeterministicStub:
    """OpenAI SSE stub; request content, never a global counter, selects the turn."""

    def __init__(self, port: int, output_content: str, delay_seconds: float) -> None:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                messages = body.get("messages") or []
                has_tool_result = any(
                    isinstance(message, dict) and message.get("role") == "tool"
                    for message in messages
                )
                if delay_seconds:
                    time.sleep(delay_seconds)
                if has_tool_result:
                    chunks = [
                        {"choices": [{"delta": {"content": "done"}}]},
                        {
                            "choices": [{"delta": {}, "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                        },
                    ]
                else:
                    call = {
                        "index": 0,
                        "id": "call_1",
                        "function": {
                            "name": "write",
                            "arguments": json.dumps(
                                {"path": DRAFT_OUTPUT, "content": output_content}
                            ),
                        },
                    }
                    chunks = [
                        {"choices": [{"delta": {"tool_calls": [call]}}]},
                        {
                            "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                        },
                    ]
                payload = _sse(chunks)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self.server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _start_node() -> dict[str, Any]:
    return {"label": "measurement input", "type": "start", "accepted_item_types": ["ref"]}


def _agent_node(after: list[str], inputs: list[str]) -> dict[str, Any]:
    return {
        "label": "real velites stub agent",
        "type": "agent",
        "capability": "resource_agent",
        "skill": {"key": SKILL_KEY, "ref": "latest"},
        "after": after,
        "inputs": inputs,
        "outputs": [DRAFT_OUTPUT],
        "execution": {
            "runtime": "velites",
            "provider": "gateway",
            "model": "stub-model",
            "thinking": "low",
        },
    }


def _code_node(key: str, after: list[str], inputs: list[str], output: str) -> dict[str, Any]:
    return {
        "label": key,
        "type": "code",
        "capability": key,
        "after": after,
        "inputs": inputs,
        "outputs": [output],
    }


def _definitions() -> dict[str, dict[str, Any]]:
    agent_nodes = {
        "_start": _start_node(),
        "agent": {**_agent_node([], []), "terminal": {"outcome": "published"}},
    }
    code_nodes = {
        "_start": _start_node(),
        "resource_code": {
            **_code_node("resource_code", [], [], "code_output.json"),
            "terminal": {"outcome": "published"},
        },
    }
    mixed_nodes = {
        "_start": _start_node(),
        "resource_intake": _code_node("resource_intake", [], [], "intake_result.json"),
        "agent": _agent_node(["resource_intake"], ["intake_result.json"]),
        "resource_publish": {
            **_code_node(
                "resource_publish",
                ["agent"],
                ["intake_result.json", DRAFT_OUTPUT],
                "publish_payload.json",
            ),
            "terminal": {"outcome": "published"},
        },
    }
    return {
        WORKSPACES["agent"]: {
            "key": WORKSPACES["agent"],
            "label": "resource agent",
            "schema_version": 2,
            "nodes": agent_nodes,
            "edges": [{"from": "_start", "to": "agent"}],
        },
        WORKSPACES["code"]: {
            "key": WORKSPACES["code"],
            "label": "resource code",
            "schema_version": 2,
            "nodes": code_nodes,
            "edges": [{"from": "_start", "to": "resource_code"}],
        },
        WORKSPACES["mixed"]: {
            "key": WORKSPACES["mixed"],
            "label": "resource mixed",
            "schema_version": 2,
            "nodes": mixed_nodes,
            "edges": [
                {"from": "_start", "to": "resource_intake"},
                {"from": "resource_intake", "to": "agent"},
                {"from": "agent", "to": "resource_publish"},
            ],
        },
        WORKSPACES["upload-backlog"]: {
            "key": WORKSPACES["upload-backlog"],
            "label": "resource upload",
            "schema_version": 2,
            "nodes": code_nodes | {"_start": _start_node()},
            "edges": [{"from": "_start", "to": "resource_code"}],
        },
    }


def _node_sources(artifact_chars: int) -> dict[str, str]:
    return {
        "resource_code": f"""from workspace_libs.node_sdk import NodeContext, entrypoint\nimport time\n\n@entrypoint\ndef run(ctx: NodeContext) -> None:\n    time.sleep(0.2)\n    ctx.artifacts.write_json("code_output.json", {{"marker": "resource-code", "payload": "x" * {artifact_chars}}})\n""",
        "resource_intake": """from workspace_libs.node_sdk import NodeContext, entrypoint\nimport time\n\n@entrypoint\ndef run(ctx: NodeContext) -> None:\n    time.sleep(0.2)\n    ctx.artifacts.write_json("intake_result.json", {"job_id": str(ctx.job.get("id", ""))})\n""",
        "resource_publish": f"""from workspace_libs.node_sdk import NodeContext, entrypoint\nimport time\n\n@entrypoint\ndef run(ctx: NodeContext) -> None:\n    time.sleep(0.2)\n    draft = ctx.artifacts.read_json_object("draft.json")\n    ctx.artifacts.write_json("publish_payload.json", {{"draft": draft, "payload": "y" * {artifact_chars}}})\n""",
    }


def seed_measurement_workspaces(dsn: str, skills_dir: Path, artifact_chars: int) -> None:
    job_db = JobQueries(dsn, jobs_dir=skills_dir / "unused-jobs")
    with write_transaction(dsn) as conn:
        conn.execute(
            "insert into external_connections(key, type, display_name, config_json)"
            " values ('measurement-ref', 'static_bearer', 'Measurement refs', '{}')"
            " on conflict(key) do nothing"
        )
    _ensure_skill_repo(skills_dir / SKILL_KEY)
    codes = NodeCodeService(dsn)
    revisions = WorkflowRevisionService(job_db)
    sources = _node_sources(artifact_chars)
    for workspace_id, definition in _definitions().items():
        if workspace_id not in {row["id"] for row in job_db.list_workspaces()}:
            job_db.create_workspace(definition["label"], workspace_id=workspace_id)
        for node in definition["nodes"].values():
            if node.get("type") == "code":
                seed_workspace_node_code(
                    codes,
                    workspace_id,
                    workspace_id,
                    node["capability"],
                    sources[node["capability"]],
                    "resource measurement",
                )
        revisions.ensure_active_revision(workspace_id, workflow_definition_from_dict(definition))


class MeasurementRun:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.work_dir = args.work_dir.resolve()
        self.output_dir = args.output_dir.resolve()
        self.velites = args.velites_bin.resolve()
        self.project = f"agent-legion-measure-{os.getpid()}"
        self.host_url = f"http://127.0.0.1:{args.host_port}"
        self.worker_url = f"http://127.0.0.1:{args.worker_port}"
        self.dsn = f"postgresql://agent_legion:measurement-only-password@127.0.0.1:{args.postgres_port}/agent_legion_measurement"
        self.skills_dir = self.work_dir / "skills"
        self.models_path = self.work_dir / "models.json"
        self.worker_config = self.work_dir / "worker.yaml"
        self.vault_key = Fernet.generate_key().decode("ascii")
        self.secrets = [
            self.vault_key,
            "measurement-secret-key",
            "measurement-stub-key",
            "measurement-only-password",
        ]
        self.env = os.environ | {
            "COMPOSE_PROJECT_NAME": self.project,
            "MEASUREMENT_POSTGRES_PORT": str(args.postgres_port),
            "MEASUREMENT_S3_PORT": str(args.s3_port),
            "MEASUREMENT_HOST_PORT": str(args.host_port),
            "MEASUREMENT_WORKER_PORT": str(args.worker_port),
            "MEASUREMENT_VAULT_KEY": self.vault_key,
            "MEASUREMENT_SKILLS_DIR": str(self.skills_dir),
            "MEASUREMENT_WORKER_CONFIG": str(self.worker_config),
            "MEASUREMENT_MODELS_PATH": str(self.models_path),
            "VELITES_BIN": str(self.velites),
            "AGENT_LEGION_SKIP_DOTENV": "1",
        }
        self.storage = S3StorageClient(
            S3Settings(
                bucket="agent-legion-measurement",
                endpoint_url=f"http://127.0.0.1:{args.s3_port}",
                access_key="measurement-access-key",
                secret_key="measurement-secret-key",
            )
        )
        self.cgroup_reader_id = ""

    def compose(self, *arguments: str, capture: bool = False) -> str:
        result = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), *arguments],
            cwd=PROJECT_ROOT,
            env=self.env,
            check=True,
            capture_output=capture,
            text=True,
        )
        return result.stdout.strip() if capture else ""

    def prepare(self) -> None:
        system = platform.system()
        if system != "Linux" and not (system == "Darwin" and self.args.allow_docker_desktop):
            raise RuntimeError(
                "real measurement requires native Linux or explicit Docker Desktop functional mode"
            )
        if not self.velites.is_file():
            raise RuntimeError(f"build velites first: {self.velites}")
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.worker_config.write_text(
            yaml.safe_dump(
                {
                    "host_url": "http://host:8000",
                    "worker_id": WORKER_ID,
                    "name": "Resource measurement Worker",
                    "disabled_runtimes": [],
                    "max_concurrency": self.args.agent_concurrency,
                    "max_code_concurrency": self.args.code_concurrency,
                    "upload_max_concurrency": self.args.upload_concurrency,
                    "claim_batch_limit": self.args.claim_batch_limit,
                    "labels": {"os": "linux", "arch": platform.machine(), "measurement": "true"},
                    "work_root": "/var/lib/agent-legion-worker",
                    "poll_interval_seconds": 0.2,
                    "heartbeat_interval_seconds": 1,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        content = json.dumps(
            {"marker": "deterministic-stub", "payload": "a" * self.args.artifact_chars}
        )
        self.models_path.write_text(
            json.dumps(
                {
                    "providers": {
                        "gateway": {
                            "api": "openai-completions",
                            "baseUrl": f"http://host.docker.internal:{self.args.stub_port}/v1",
                            "apiKey": "measurement-stub-key",
                            "models": ["stub-model"],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        self.models_path.chmod(0o600)
        self.stub = DeterministicStub(self.args.stub_port, content, self.args.stub_delay_seconds)
        if not self.args.skip_build:
            self.compose("build", "host", "worker")

    def _wait(self, description: str, probe: Any, timeout: float = 120) -> Any:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                value = probe()
                if value:
                    return value
            except ContractViolationError:
                raise
            except (
                requests.RequestException,
                OSError,
                RuntimeError,
                ValueError,
                subprocess.CalledProcessError,
            ) as exc:
                last_error = exc
            time.sleep(0.2)
        raise RuntimeError(f"timed out waiting for {description}: {last_error or 'not ready'}")

    def _wait_http(self, url: str) -> None:
        self._wait(url, lambda: requests.get(url, timeout=2).status_code == 200)

    def _control_token(self) -> str:
        return self.compose(
            "exec",
            "-T",
            "worker",
            "cat",
            "/var/lib/agent-legion-worker-control/control_token",
            capture=True,
        )

    def _worker_request(
        self, method: str, path: str, token: str, **kwargs: Any
    ) -> requests.Response:
        response = requests.request(
            method,
            f"{self.worker_url}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            **kwargs,
        )
        response.raise_for_status()
        return response

    def _wait_worker_process(self, token: str) -> None:
        def ready() -> bool:
            status = self._worker_request("GET", "/api/status", token).json()
            return bool(status.get("worker_running")) and not status.get("failed")

        self._wait("Worker execution process", ready)

    def _wait_registered(self, session: requests.Session) -> None:
        def registered() -> bool:
            response = session.get(f"{self.host_url}/api/agent-workers", timeout=5)
            response.raise_for_status()
            for worker in response.json().get("workers", []):
                if worker.get("worker_id") != WORKER_ID:
                    continue
                scopes = set(worker.get("allowed_workspaces") or [])
                return (
                    scopes == set(WORKSPACES.values())
                    and int(worker.get("max_code_concurrency") or 0) == self.args.code_concurrency
                    and "velites" in (worker.get("runtimes") or [])
                )
            return False

        self._wait("remote agent/code registration", registered)

    def _enable_claim(self, token: str, session: requests.Session) -> None:
        self._worker_request("PUT", "/api/config", token, json={"claim_enabled": True})
        self._wait_worker_process(token)
        self._wait_registered(session)

    def _create_run(
        self, session: requests.Session, scenario: str, iteration: str
    ) -> dict[str, Any]:
        workspace_id = WORKSPACES[scenario]
        items = [
            {
                "type": "ref",
                "connection_key": "measurement-ref",
                "external_id": f"measurement-{iteration}-{scenario}-{index}",
            }
            for index in range(self.args.jobs_per_wave)
        ]
        response = session.post(
            f"{self.host_url}/api/workspaces/{workspace_id}/runs",
            json={"items": items},
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "workspace_id": workspace_id,
            "run_id": payload["run"]["id"],
            "job_ids": payload["job_ids"],
        }

    def _await_run(
        self, session: requests.Session, scenario: str, pending: dict[str, Any]
    ) -> dict[str, Any]:
        if len(pending["job_ids"]) != self.args.jobs_per_wave:
            raise RuntimeError(f"{scenario} did not create the expected measurement wave")

        def completed() -> bool:
            detail = session.get(
                f"{self.host_url}/api/workspaces/{pending['workspace_id']}/runs/{pending['run_id']}",
                timeout=10,
            )
            detail.raise_for_status()
            stats = detail.json()["job_stats"]
            by_status = stats["by_status"]
            failed = sum(int(by_status.get(status, 0)) for status in ("failed", "cancelled"))
            if failed:
                raise RuntimeError(f"{scenario} has {failed} failed/cancelled jobs")
            return stats["total"] == len(pending["job_ids"]) and int(
                by_status.get("completed", 0)
            ) == len(pending["job_ids"])

        self._wait(f"{scenario} run completion", completed, timeout=self.args.job_timeout_seconds)

        def execution_ready() -> dict[str, Any] | None:
            try:
                return self._verify_remote(pending["job_ids"], scenario)
            except ExecutionReadinessError:
                return None

        evidence = self._wait(
            f"{scenario} done execution identities",
            execution_ready,
            timeout=self.args.job_timeout_seconds,
        )
        return {
            "run_id": pending["run_id"],
            "job_count": len(pending["job_ids"]),
            **evidence,
        }

    def _submit(self, session: requests.Session, scenario: str, iteration: str) -> dict[str, Any]:
        return self._await_run(session, scenario, self._create_run(session, scenario, iteration))

    def _verify_remote(self, job_ids: list[str], scenario: str) -> dict[str, Any]:
        workspace_id = WORKSPACES[scenario]
        nodes = {
            key: node
            for key, node in _definitions()[workspace_id]["nodes"].items()
            if node.get("type") in {"agent", "code"}
        }
        return verify_remote_uploads(
            dsn=self.dsn,
            storage=self.storage,
            workspace_id=workspace_id,
            nodes=nodes,
            job_ids=job_ids,
            worker_id=WORKER_ID,
        )

    def run_iteration(self, mode: str, repetition: int) -> dict[str, Any]:
        iteration = f"{mode}-{repetition}"
        iteration_dir = self.output_dir / iteration
        iteration_dir.mkdir(parents=True, exist_ok=True)
        sampler: CgroupSampler | None = None
        result: dict[str, Any] = {"iteration": iteration, "success": False, "scenarios": {}}
        try:
            down_args = ("down", "--remove-orphans") + (("-v",) if mode == "cold" else ())
            self.compose(*down_args)
            self.compose("up", "-d", "postgres", "seaweedfs")
            self._wait_http(f"http://127.0.0.1:{self.args.s3_port}/healthz")
            self._wait(
                "PostgreSQL",
                lambda: self.compose(
                    "exec",
                    "-T",
                    "postgres",
                    "pg_isready",
                    "-U",
                    "agent_legion",
                    "-d",
                    "agent_legion_measurement",
                    capture=True,
                ),
            )
            seed_measurement_workspaces(self.dsn, self.skills_dir, self.args.artifact_chars)
            ensure_measurement_bucket(self)
            self.compose("up", "-d", "host")
            self._wait_http(f"{self.host_url}/api/health")
            session = ensure_admin_session(self.host_url)
            self.compose("up", "-d", "worker")
            container = self.compose("ps", "-q", "worker", capture=True)
            pid = int(
                subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Pid}}", container],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
            )
            reader = self._wait("Worker cgroup reader", lambda: cgroup_reader(self, pid))
            sampler = CgroupSampler(
                reader,
                iteration_dir / "samples.jsonl",
                pids_interval_seconds=self.args.pids_interval_ms / 1000,
                memory_interval_seconds=self.args.memory_interval_ms / 1000,
            )
            sampler.mark(iteration, "register")
            sampler.start()
            write_iteration_metadata(self, iteration_dir, iteration, container)
            self._wait_http(f"{self.worker_url}/api/health")
            control_token = self._control_token()
            self.secrets.append(control_token)

            for workspace_id in sorted(set(WORKSPACES.values())):
                _token_id, register_token = AgentRegisterTokenStore(self.dsn).issue_register_token(
                    workspace_id=workspace_id, label=f"measurement {iteration}"
                )
                self.secrets.append(register_token)
                self._worker_request(
                    "POST",
                    "/api/register-tokens",
                    control_token,
                    json={"register_token": register_token},
                )
            self._enable_claim(control_token, session)
            for workspace_id in set(WORKSPACES.values()):
                WorkspaceWorkerControl(self.dsn).resume(workspace_id)
            sampler.mark(iteration, "idle-registered")
            time.sleep(self.args.idle_seconds)

            for scenario in ("agent", "code", "mixed", "upload-backlog"):
                sampler.mark(iteration, scenario)
                if scenario == "upload-backlog":
                    result["scenarios"][scenario] = measure_upload_backlog(
                        self, session, iteration, control_token, sampler
                    )
                else:
                    result["scenarios"][scenario] = self._submit(session, scenario, iteration)

            sampler.mark(iteration, "pending-restart")
            pending = self._worker_request(
                "PUT", "/api/config", control_token, json={"disabled_runtimes": ["velites"]}
            ).json()
            rows = pending.get("config", {}).get("runtime_status", [])
            if not any(
                row.get("runtime") == "velites" and row.get("pending_restart") for row in rows
            ):
                raise RuntimeError("velites pending-restart state was not observable")
            result["scenarios"]["pending-restart"] = {"observed": True}
            time.sleep(self.args.idle_seconds)
            self._worker_request(
                "PUT", "/api/config", control_token, json={"disabled_runtimes": []}
            )
            self._enable_claim(control_token, session)

            sampler.mark(iteration, "executor-restart")
            self._worker_request("POST", "/api/restart", control_token)
            self._enable_claim(control_token, session)
            time.sleep(self.args.idle_seconds)
            result["scenarios"]["executor-restart"] = {"observed": True}

            sampler.mark(iteration, "container-restart")
            self.compose("restart", "worker")
            self._wait_http(f"{self.worker_url}/api/health")
            self._enable_claim(control_token, session)
            time.sleep(self.args.idle_seconds)
            result["scenarios"]["container-restart"] = {"observed": True}
            result["success"] = True
        except Exception as exc:
            result["error"] = sanitize_log_text(f"{type(exc).__name__}: {exc}", self.secrets)
            LOGGER.error("%s failed: %s", iteration, result["error"])
        finally:
            if sampler is not None:
                sampler.mark(iteration, "teardown")
                errors = sampler.stop()
                summary = sampler.write_summary(iteration_dir / "summary.json")
                if errors:
                    result["success"] = False
                    result["error"] = f"sampling failed: {errors}"
                result["sample_counts"] = (
                    summary["groups"]
                    .get(f"{iteration}/idle-registered", {})
                    .get("sample_counts", {})
                )
            try:
                write_sanitized_logs(self, iteration_dir)
            except (OSError, subprocess.CalledProcessError) as exc:
                result["success"] = False
                result["error"] = f"log collection failed: {exc}"
            if self.cgroup_reader_id:
                subprocess.run(
                    ["docker", "rm", "-f", self.cgroup_reader_id],
                    check=False,
                    capture_output=True,
                )
                self.cgroup_reader_id = ""
            try:
                self.compose("down", "--remove-orphans")
            except subprocess.CalledProcessError as exc:
                result["success"] = False
                result["error"] = f"stack cleanup failed: {exc}"
            (iteration_dir / "result.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        return result


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir", type=Path, default=Path("data/worker-resource-measurement/work")
    )
    parser.add_argument("--output-dir", type=Path, default=Path("worker-resource-results/latest"))
    parser.add_argument("--velites-bin", type=Path, default=Path("data/bin/velites"))
    parser.add_argument("--agent-concurrency", type=_positive, default=2)
    parser.add_argument("--code-concurrency", type=_positive, default=2)
    parser.add_argument("--upload-concurrency", type=_positive, default=2)
    parser.add_argument("--claim-batch-limit", type=_positive, default=8)
    parser.add_argument("--jobs-per-wave", type=_positive, default=4)
    parser.add_argument("--artifact-kib", type=_positive, default=64)
    parser.add_argument("--stub-delay-seconds", type=float, default=0.05)
    parser.add_argument("--idle-seconds", type=float, default=2.0)
    parser.add_argument("--job-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--backlog-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--pids-interval-ms", type=_positive, default=100)
    parser.add_argument("--memory-interval-ms", type=_positive, default=500)
    parser.add_argument("--cold-repetitions", type=int, default=1)
    parser.add_argument("--warm-repetitions", type=int, default=2)
    parser.add_argument("--postgres-port", type=int, default=55432)
    parser.add_argument("--s3-port", type=int, default=58333)
    parser.add_argument("--host-port", type=int, default=58080)
    parser.add_argument("--worker-port", type=int, default=58787)
    parser.add_argument("--stub-port", type=int, default=58081)
    parser.add_argument("--cgroup-reader-port", type=int, default=58082)
    parser.add_argument("--allow-docker-desktop", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args(argv)
    if (
        args.cold_repetitions < 0
        or args.warm_repetitions < 0
        or args.cold_repetitions + args.warm_repetitions == 0
    ):
        parser.error("cold/warm repetitions must be non-negative and not both zero")
    if (
        args.stub_delay_seconds < 0
        or args.idle_seconds < 0
        or args.job_timeout_seconds <= 0
        or args.backlog_timeout_seconds <= 0
    ):
        parser.error("delays must be non-negative and timeouts must be positive")
    args.artifact_chars = args.artifact_kib * 1024
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run = MeasurementRun(args)
    run.stub = None
    reports: list[dict[str, Any]] = []
    try:
        run.prepare()
        for mode, count in (("cold", args.cold_repetitions), ("warm", args.warm_repetitions)):
            for repetition in range(1, count + 1):
                report = run.run_iteration(mode, repetition)
                reports.append(report)
                if not report["success"]:
                    break
            if reports and not reports[-1]["success"]:
                break
    except Exception as exc:
        reports.append(
            {
                "iteration": "prepare",
                "success": False,
                "error": sanitize_log_text(f"{type(exc).__name__}: {exc}", run.secrets),
            }
        )
        LOGGER.exception("measurement preparation failed")
    finally:
        if run.stub is not None:
            run.stub.close()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "success": bool(reports) and all(item["success"] for item in reports),
        "iterations": reports,
    }
    (args.output_dir / "run-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
