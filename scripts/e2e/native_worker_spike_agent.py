from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.dispatch import AgentDispatchService
from server.app.services.agent_node_profile_types import profile_from_node
from server.app.services.artifact_store import ArtifactStore
from server.app.settings import Settings
from server.app.skills.skill_roots import skills_root
from server.app.workflows.schema import WorkflowNode, WorkflowNodeExecution, WorkflowNodeSkill

SKILL_KEY = "native-spike/probe"


def _git(repo: Path, *args: str) -> None:
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="native-spike",
        GIT_AUTHOR_EMAIL="native-spike@example.invalid",
        GIT_COMMITTER_NAME="native-spike",
        GIT_COMMITTER_EMAIL="native-spike@example.invalid",
    )
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def _make_skill_repo() -> Path:
    repo = skills_root() / SKILL_KEY
    if repo.exists():
        shutil.rmtree(repo)
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "SKILL.md").write_text(
        "# Native spike\nRun the bash probe, write agent_result.json, then stop.\n",
        encoding="utf-8",
    )
    (repo / "references").mkdir()
    (repo / "references" / "output-contract.md").write_text(
        "# Outputs\n- agent_result.json: deterministic completion marker.\n"
        "- agent_bash_evidence.json: all native sandbox checks must pass.\n",
        encoding="utf-8",
    )
    (repo / "scripts").mkdir()
    (repo / "scripts" / "validate_output.py").write_text(
        textwrap.dedent(
            """\
            import json
            import sys
            from pathlib import Path

            job_dir = Path(sys.argv[1])
            result = json.loads((job_dir / "agent_result.json").read_text())
            evidence = json.loads((job_dir / "agent_bash_evidence.json").read_text())
            if result != {"agent": "native-stub", "complete": True}:
                raise SystemExit("invalid agent result")
            if not evidence.get("checks") or not all(evidence["checks"].values()):
                raise SystemExit("invalid agent bash evidence")
            """
        ),
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "native spike fixture", "--no-gpg-sign")
    return repo


def enqueue_agent_job(
    *,
    settings: Settings,
    agent_config: dict[str, Any],
    broker: AgentExecutionBroker,
    store: ArtifactStore,
) -> None:
    _make_skill_repo()
    job_dir = settings.jobs_dir / "worker-agent"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "agent_probe_config.json").write_text(json.dumps(agent_config), encoding="utf-8")
    payload = Path(__file__).with_name("native_worker_spike_payload.py")
    shutil.copyfile(payload, job_dir / "native_worker_spike_payload.py")
    node = WorkflowNode(
        key="agent",
        label="agent",
        capability="native_agent",
        node_type="agent",
        outputs=["agent_result.json", "agent_bash_evidence.json"],
        skill=WorkflowNodeSkill(key=SKILL_KEY, ref="latest"),
        execution=WorkflowNodeExecution(
            runtime="velites", provider="gateway", model="stub-model", thinking="low"
        ),
        tools=("read", "write", "bash"),
        requires_labels={},
        config_schema={},
    )
    profile = profile_from_node(node)
    agents = AgentDispatchService(settings, broker, store)
    if not agents.enqueue(
        agent_id=node.key,
        definition=profile.dispatch_definition,
        workspace={"id": "native-spike"},
        job={"id": "worker-agent"},
        workflow_key="native-spike",
        node=node,
        job_dir=job_dir,
        log_path=settings.logs_dir / "worker-agent.log",
        inputs=("agent_probe_config.json", "native_worker_spike_payload.py"),
        node_config={"sandbox_network": True, "timeout_seconds": 120},
        profile_source="node",
    ):
        raise RuntimeError("failed to enqueue the real Worker agent request")
