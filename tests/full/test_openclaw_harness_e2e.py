"""openclaw runtime 端到端（full lane）：fake openclaw 二进制。

不做真实 LLM 调用：fake 二进制模拟 `openclaw agent --local --json` 的形态
（读 --message-file、写产物、stdout 输出一次性 pretty-printed envelope），
经生产 broker 链路（dispatch 冻结 manifest → claim 重渲染 → 按
command_spec 真跑）断言 argv 形状、产物落盘与 Worker 侧事件合成
（worker/openclaw_events.py：envelope → pi 子集事件）。
"""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

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
from worker.openclaw_events import synthesize_openclaw_events

OUTPUT_NAME = "keywords_raw.json"

# fake openclaw：解析 --message-file，写产物，输出一次性 envelope（对齐实测
# 的 pretty-printed 形态：writeRuntimeJson(space=2)）。
FAKE_OPENCLAW = """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

argv = sys.argv[1:]
message_file = Path(argv[argv.index("--message-file") + 1])
prompt = message_file.read_text(encoding="utf-8")
assert "Job ID: job-1" in prompt
Path("keywords_raw.json").write_text('{"questions": []}', encoding="utf-8")
print(json.dumps({
    "payloads": [{"text": "done", "mediaUrl": None}],
    "meta": {"durationMs": 42, "transport": "embedded"},
}, indent=2))
"""


class _LocalSkillManager:
    """Test double for SkillManager: serves a pre-built skill tree from base_dir."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def get_skill_dir(self, skill: str, execution_id: str) -> Path:
        return self.base_dir / skill

    def cleanup_execution(self, execution_id: str) -> None:
        return None


@pytest.mark.full_gate
def test_openclaw_runtime_agent_worker_chain_end_to_end(tmp_path: Path, job_db) -> None:
    """runtime=openclaw 全链路：dispatch → claim → fake 二进制真跑 → 事件合成。"""
    fake = tmp_path / "bin" / "openclaw"
    fake.parent.mkdir(parents=True)
    fake.write_text(FAKE_OPENCLAW, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    skill_root = tmp_path / "skills"
    make_pi_skill(skill_root, "demo_workflow/generate_key_info")
    skill_dir = skill_root / "demo_workflow/generate_key_info"

    definition = AgentDefinition(
        capability="generate",
        runtime="openclaw",
        skill="demo_workflow/generate_key_info",
    )
    replace_agent_catalog("test-workspace", {"openclaw-agent": definition})
    _insert_job_rows(
        job_db,
        job_id="job-1",
        node_key="generate",
        limit=5,
        workspace_id="test-workspace",
        agent_id="openclaw-agent",
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
        execution=WorkflowNodeExecution(provider="kimi", model="kimi-code", thinking="low"),
    )
    enqueued = service.enqueue(
        agent_id="openclaw-agent",
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

    with job_db.connect() as conn:
        row = conn.execute(
            "select manifest_json from agent_execution_requests where job_id='job-1'"
        ).fetchone()
    frozen = json.loads(row["manifest_json"])
    assert frozen["runtime"] == "openclaw"
    assert frozen["execution"] == {
        "binary": "openclaw",
        "provider": "kimi",
        "model": "kimi-code",
        "thinking": "low",
        "timeout_seconds": 1800,
        "no_sandbox": False,
    }

    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    registry.issue_token(
        worker_id="worker-o",
        name="worker-o",
        runtimes=["openclaw"],
        max_concurrency=1,
    )
    claim = broker.claim("worker-o")
    assert claim is not None
    command = claim.manifest["command_spec"]["command"]
    assert command[:2] == ["openclaw", "agent"]
    assert "--local" in command and "--json" in command
    # provider 拼成 provider/model 组合串（契约 semantics）。
    assert command[command.index("--model") + 1] == "kimi/kimi-code"
    assert command[command.index("--thinking") + 1] == "low"
    assert command[command.index("--timeout") + 1] == "1800"

    # Worker 执行语义：占位符替换后 Popen（worker/execution/prepare.py 同款）。
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
    argv = [str(fake), *command[1:]]
    for placeholder, value in substitutions.items():
        argv = [part.replace(placeholder, value) for part in argv]
    proc = subprocess.run(argv, cwd=job_dir, capture_output=True, text=True, timeout=60)

    assert proc.returncode == 0, proc.stderr[-500:]
    assert (job_dir / OUTPUT_NAME).is_file()
    # stdout 是一次性 pretty-printed envelope（非流式事件）。
    envelope = json.loads(proc.stdout)
    assert envelope["payloads"][0]["text"] == "done"

    # Worker 侧合成：envelope → pi 子集事件（run.py 的 openclaw 分支同款）。
    events_path = job_dir / "runs" / "generate" / "worker"
    events_path.mkdir(parents=True)
    events_file = events_path / "events.jsonl"
    events_file.write_text(proc.stdout, encoding="utf-8")
    synthesize_openclaw_events(events_file, session_id=claim.execution_id, exit_code=0)
    events = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
        if line.startswith('{"type": "')
    ]
    types = [event["type"] for event in events]
    assert types[0] == "session"
    assert types[-1] == "agent_end"
    assert "error" not in events[-1]
    assert events[4]["message"]["content"] == [{"type": "text", "text": "done"}]

    broker.release_slot(claim.execution_id, "worker-o", claim.lease_id)
    broker.mark_done(claim.execution_id, "worker-o", claim.lease_id, {"status": "completed"})
    with job_db.connect() as conn:
        state = conn.execute(
            "select state from agent_execution_requests where execution_id=%s",
            (claim.execution_id,),
        ).fetchone()
    assert state["state"] == "done"
