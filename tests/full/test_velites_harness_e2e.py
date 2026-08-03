"""velites flavor 端到端（full lane）：真 velites 二进制 + Python stub SSE gateway。

不做真实 LLM 调用：stub server 模拟 OpenAI chat completions 流式响应
（一轮 toolCall + 一轮 stop，均带 usage chunk），PiRunner 以 flavor=velites
跑完整节点执行，断言事件流、产物落盘、run.json、outputs_validation 事件与
token 计量落库。环境无 cargo 且无可复用 debug 产物时 skip 而非 fail。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from server.app.jobs import JobQueries
from server.app.workflows.pi_runner import PiRunner
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


@pytest.mark.full_gate
def test_velites_flavor_end_to_end(tmp_path: Path) -> None:
    binary = _velites_binary()
    gateway = _StubGateway()
    try:
        skill_root = tmp_path / "skills"
        make_pi_skill(skill_root, "question_comprehension_info/generate_key_info")
        skill_dir = skill_root / "question_comprehension_info/generate_key_info"

        runner = PiRunner.from_config(
            {
                "binary": str(binary),
                "flavor": "velites",
                "provider": "gateway",
                "model": "stub-model",
                "timeout_seconds": 120,
                "environment": {
                    "VELITES_BASE_URL": gateway.base_url,
                    "VELITES_API_KEY": "stub-key",
                },
            },
            skill_root=skill_root,
        )

        job_db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
        workspace = job_db.create_workspace(
            "velites_e2e_ws", default_workflow_key="question_comprehension_info"
        )
        job = job_db.create_job(
            workflow_key="question_comprehension_info",
            source_type="question",
            source_id="Q-velites",
            batch_id="b-velites",
            title="Q-velites",
            node_keys=["extract_keywords"],
            workspace_id=workspace["id"],
        )
        job_dir = tmp_path / job["storage_dir"]
        job_dir.mkdir(parents=True, exist_ok=True)

        result = runner.run(
            job=job,
            node_key="extract_keywords",
            skill_dir=skill_dir,
            inputs=["questions_parsed.json"],
            outputs=[OUTPUT_NAME],
            job_db=job_db,
            job_dir=job_dir,
            node_config={"max_turns": 5},
        )
    finally:
        gateway.close()

    assert result.status == "completed"
    assert result.exit_code == 0
    assert result.error_message == ""

    # argv：velites flag 序列，无 pi 专属 flag；预算/timeout/输出自检下发。
    command = result.command
    assert command[0] == str(binary)
    assert command[command.index("--provider") + 1] == "gateway"
    assert command[command.index("--model") + 1] == "stub-model"
    assert command[command.index("--max-turns") + 1] == "5"
    assert command[command.index("--timeout-seconds") + 1] == "120"
    assert command[command.index("--require-output") + 1] == OUTPUT_NAME
    for flag in PI_ONLY_FLAGS:
        assert flag not in command

    # stub 确实被调了两次（toolCall 一轮 + stop 一轮），且走 SSE。
    assert len(gateway.bodies) == 2
    assert gateway.bodies[0]["model"] == "stub-model"
    assert gateway.bodies[0]["stream"] is True

    # 事件流：类型序列与 velites/json1 契约一致，无 delta 事件。
    events = [
        json.loads(line)
        for line in (result.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    types = [event["type"] for event in events]
    assert types[0] == "session"
    assert types[1] == "agent_start"
    assert types[-2:] == ["outputs_validation", "agent_end"]
    assert types.count("turn_start") == 2
    assert "tool_execution_start" in types
    assert "tool_execution_end" in types
    assert "message_update" not in types
    assert "tool_execution_update" not in types
    tool_start = next(e for e in events if e["type"] == "tool_execution_start")
    assert tool_start["toolName"] == "write"
    validation = next(e for e in events if e["type"] == "outputs_validation")
    assert validation["missing"] == []
    usages = [
        e["message"]["usage"]
        for e in events
        if e["type"] == "message_end" and e["message"].get("usage")
    ]
    # usage.input 口径 = prompt_tokens - cacheRead（pi 口径，缓存不双重计费）：
    # 第一跳 11 - 3 = 8，第二跳无缓存 23。
    assert [u["input"] for u in usages] == [8, 23]

    # 声明产物落盘 + run.json。
    assert (job_dir / OUTPUT_NAME).is_file()
    run_meta = json.loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_meta["exit_code"] == 0
    assert run_meta["model"] == {"provider": "gateway", "model": "stub-model", "thinking": "low"}

    # token 计量落库（node_run_token_usage 链路）。
    with job_db.connect() as conn:
        row = conn.execute("select * from node_run_token_usage").fetchone()
    assert row is not None
    assert row["input_tokens"] == 31
    assert row["output_tokens"] == 12
    assert row["cache_read_tokens"] == 3
    assert row["total_tokens"] == 46
    assert row["provider"] == "gateway"
    assert row["model"] == "stub-model"
