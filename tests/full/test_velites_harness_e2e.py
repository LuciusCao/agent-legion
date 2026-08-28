"""velites runtime 端到端（full lane）：真 velites 二进制 + Python stub SSE gateway。

不做真实 LLM 调用：stub server 模拟 OpenAI chat completions 流式响应
（一轮 toolCall + 一轮 stop，均带 usage chunk），经生产 broker 链路
（dispatch 冻结 manifest → claim 重渲染 → 按 command_spec 真跑二进制）
断言事件流、产物落盘与 outputs_validation 事件。
环境无 cargo 且无可复用 debug 产物时 skip 而非 fail。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.dispatch import AgentDispatchService
from server.app.agent_catalog import AgentDefinition
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.services.artifact_store import ArtifactStore
from server.app.settings import Settings
from server.app.workflows.schema import WorkflowNode, WorkflowNodeExecution
from tests.helpers import replace_agent_catalog
from tests.helpers.agent_worker_api import insert_job_rows as _insert_job_rows
from tests.helpers.executor_worker import make_pi_skill
from tests.postgres_support import TEST_DATABASE_URL

REPO_ROOT = Path(__file__).resolve().parents[2]
VELITES_BINARY = REPO_ROOT / "velites" / "target" / "debug" / "velites"

OUTPUT_NAME = "keywords_raw.json"
PI_ONLY_FLAGS = (
    "--no-context-files",
    "--no-extensions",
    "--no-prompt-templates",
    "--no-skills",
    "--approve",
)


def _velites_binary() -> Path:
    if VELITES_BINARY.is_file():
        return VELITES_BINARY
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("no prebuilt velites binary and cargo is not available")
    proc = subprocess.run(
        [cargo, "build", "--manifest-path", str(REPO_ROOT / "velites" / "Cargo.toml")],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if proc.returncode != 0 or not VELITES_BINARY.is_file():
        pytest.skip(f"velites build failed: {proc.stderr[-400:]}")
    return VELITES_BINARY


def _sse(chunks: list[dict[str, Any]]) -> bytes:
    lines = [f"data: {json.dumps(chunk)}\n" for chunk in chunks]
    lines.append("data: [DONE]\n")
    return ("\n".join(lines) + "\n").encode()


class _StubGateway:
    """OpenAI 兼容 SSE stub：第 1 次请求返回 write toolCall，第 2 次返回 stop。"""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 (http.server API)
                length = int(self.headers.get("Content-Length") or 0)
                gateway.bodies.append(json.loads(self.rfile.read(length) or b"{}"))
                if len(gateway.bodies) == 1:
                    payload = _sse(
                        [
                            {
                                "choices": [
                                    {
                                        "delta": {
                                            "tool_calls": [
                                                {
                                                    "index": 0,
                                                    "id": "call_1",
                                                    "function": {
                                                        "name": "write",
                                                        "arguments": json.dumps(
                                                            {
                                                                "path": OUTPUT_NAME,
                                                                "content": '{"questions": []}',
                                                            }
                                                        ),
                                                    },
                                                }
                                            ]
                                        }
                                    }
                                ]
                            },
                            {
                                "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                                "usage": {
                                    "prompt_tokens": 11,
                                    "completion_tokens": 7,
                                    "prompt_cache_hit_tokens": 3,
                                },
                            },
                        ]
                    )
                else:
                    payload = _sse(
                        [
                            {"choices": [{"delta": {"content": "done"}}]},
                            {
                                "choices": [{"delta": {}, "finish_reason": "stop"}],
                                "usage": {"prompt_tokens": 23, "completion_tokens": 5},
                            },
                        ]
                    )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def close(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()


class _LocalSkillManager:
    """Test double for SkillManager: serves a pre-built skill tree from base_dir."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def get_skill_dir(self, skill: str, execution_id: str) -> Path:
        return self.base_dir / skill

    def cleanup_execution(self, execution_id: str) -> None:
        return None


@pytest.mark.full_gate
def test_velites_runtime_agent_worker_chain_end_to_end(tmp_path: Path, job_db) -> None:
    """runtime=velites 全链路：dispatch 冻结 manifest → claim 重渲染 → 按
    command_spec 真跑 velites 二进制。provider/model 来自节点 execution 覆盖。"""
    binary = _velites_binary()
    gateway = _StubGateway()
    try:
        skill_root = tmp_path / "skills"
        make_pi_skill(skill_root, "demo_workflow/generate_key_info")
        skill_dir = skill_root / "demo_workflow/generate_key_info"

        definition = AgentDefinition(
            capability="generate",
            runtime="velites",
            skill="demo_workflow/generate_key_info",
        )
        replace_agent_catalog("test-workspace", {"velites-agent": definition})
        _insert_job_rows(
            job_db,
            job_id="job-1",
            node_key="generate",
            limit=5,
            workspace_id="test-workspace",
            agent_id="velites-agent",
        )
        job_dir = tmp_path / "jobs" / "job-1"
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "questions_parsed.json").write_text('{"questions": []}', encoding="utf-8")

        settings = Settings(
            root_dir=tmp_path,
            data_dir=tmp_path,
            videos_dir=tmp_path / "videos",
            logs_dir=tmp_path / "logs",
            packages_dir=tmp_path / "packages",
            jobs_dir=tmp_path / "jobs",
            config={},
        )
        bundle_dir = tmp_path / "bundles"
        bundle_dir.mkdir()
        broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir, data_dir=tmp_path)
        store = ArtifactStore(tmp_path / "artifacts", TEST_DATABASE_URL)
        service = AgentDispatchService(settings, broker, store)
        service.skill_manager = _LocalSkillManager(skill_root)

        node = WorkflowNode(
            key="generate",
            label="generate",
            capability="generate",
            outputs=[OUTPUT_NAME],
            execution=WorkflowNodeExecution(provider="gateway", model="stub-model", thinking="low"),
        )
        enqueued = service.enqueue(
            agent_id="velites-agent",
            definition=definition,
            workspace={"id": "test-workspace"},
            job={"id": "job-1"},
            workflow_key="questions",
            node=node,
            job_dir=job_dir,
            log_path=tmp_path / "job-1.log",
            inputs=("questions_parsed.json",),
        )
        assert enqueued is True

        # manifest 入队即冻结：runtime 钉死命令构建器，execution 块携带节点覆盖。
        with job_db.connect() as conn:
            row = conn.execute(
                "select manifest_json from agent_execution_requests where job_id='job-1'"
            ).fetchone()
        frozen = json.loads(row["manifest_json"])
        assert frozen["runtime"] == "velites"
        assert frozen["execution"] == {
            "binary": "velites",
            "provider": "gateway",
            "model": "stub-model",
            "thinking": "low",
            "timeout_seconds": 1800,
            "no_sandbox": False,
        }

        registry = AgentWorkerRegistry(TEST_DATABASE_URL)
        registry.issue_token(
            worker_id="worker-v",
            name="worker-v",
            runtimes=["velites"],
            max_concurrency=1,
        )
        claim = broker.claim("worker-v")
        assert claim is not None
        # claim 重渲染与冻结 manifest 一致，产出 velites argv。
        assert claim.manifest["execution"]["provider"] == "gateway"
        command = claim.manifest["command_spec"]["command"]
        assert command[0] == "velites"
        for flag in PI_ONLY_FLAGS:
            assert flag not in command
        assert command[command.index("--require-output") + 1] == OUTPUT_NAME

        # Worker 执行语义：占位符替换后 Popen（worker/execution_prepare.py 同款）。
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text(claim.manifest["command_spec"]["prompt"], encoding="utf-8")
        substitutions = {
            "{job_dir}": str(job_dir),
            "{skill_dir}": str(skill_dir),
            "{session_dir}": str(session_dir),
            "{session_name}": "job-1:generate:e2e",
            "{prompt_file}": str(prompt_file),
        }
        # manifest 里的 binary 是 runtime 常量名；真跑时替换成本地构建的二进制路径。
        argv = [str(binary), *command[1:]]
        for placeholder, value in substitutions.items():
            argv = [part.replace(placeholder, value) for part in argv]
        # gateway provider 走 config::resolve()（env 凭据）的前提是 models
        # registry 不存在；开发机上 ~/.velites/models.json 存在时 resolve
        # 会先撞上 "provider gateway is not configured"。指向不存在的路径
        # 强制走迁移桥分支，与 test_velites_controllability 的隔离先例一致。
        env = {
            **os.environ,
            "VELITES_BASE_URL": gateway.base_url,
            "VELITES_API_KEY": "stub-key",
            "VELITES_MODELS_PATH": str(tmp_path / "no-models.json"),
        }
        proc = subprocess.run(
            argv, cwd=job_dir, env=env, capture_output=True, text=True, timeout=300
        )

        assert proc.returncode == 0, proc.stderr[-500:]
        assert (job_dir / OUTPUT_NAME).is_file()
        # json 模式事件流走 stdout（Host/Worker 消费侧），保持 pi 兼容子集。
        events = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        types = [event["type"] for event in events]
        assert types[0] == "session"
        assert types[-2:] == ["outputs_validation", "agent_end"]
        assert "message_update" not in types
        assert "tool_execution_update" not in types
        # session mirror 落在 --session-dir 下。
        assert (session_dir / "session.jsonl").is_file()

        broker.release_slot(claim.execution_id, "worker-v", claim.lease_id)
        broker.mark_done(claim.execution_id, "worker-v", claim.lease_id, {"status": "completed"})
        with job_db.connect() as conn:
            state = conn.execute(
                "select state from agent_execution_requests where execution_id=%s",
                (claim.execution_id,),
            ).fetchone()
        assert state["state"] == "done"
    finally:
        gateway.close()
