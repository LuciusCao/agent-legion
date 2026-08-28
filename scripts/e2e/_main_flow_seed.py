"""Seed the e2e main-flow workspace for the browser smoke (Phase 4B).

A dedicated hybrid-DAG workspace the demo workflow cannot provide (the demo
needs a real LLM for four Agent nodes):

    _start(ref) → intake(code) → draft(velites Agent, stub gateway) → publish(code)

Everything is seeded through the same production services the demo seed
uses — workspace-scoped node_code (DB published text), a workspace-scoped
Agent definition (runtime=velites, skill locked to a local git repo created
under the e2e data dir), an active workflow revision (which materializes the
Agent route), and a resume of the workspace's dispatch control (every app
startup resets all workspaces to paused by design).

The demo seed is untouched; this module only ADDS one workspace.
"""

from __future__ import annotations

import datetime
import logging
import subprocess
from pathlib import Path
from typing import Any

from server.app.agent_catalog import AgentDefinition
from server.app.jobs import JobQueries
from server.app.services.agent_service import AgentService
from server.app.services.node_code_seeding import seed_workspace_node_code
from server.app.services.node_codes import NodeCodeService
from server.app.services.skill_source_store import SkillSourceStore
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.skills.config import LockedSkillSource, SkillsConfig, SkillsLock, SkillSourceConfig
from server.app.worker_control import WorkspaceWorkerControl
from server.app.workflows.loader import workflow_definition_from_dict

logger = logging.getLogger(__name__)

WORKSPACE_ID = "e2e_main_flow"
WORKSPACE_NAME = "E2E 主流程"
AGENT_ID = "e2e-stub-agent-v1"
AGENT_CAPABILITY = "e2e_draft"
SKILL_KEY = "e2e-main-flow/stub-agent"
SKILL_REF = "main"

INTAKE_OUTPUT = "intake_result.json"
DRAFT_OUTPUT = "draft.json"
PUBLISH_OUTPUT = "publish_payload.json"

# Canned payload the stub gateway's write toolCall lands as DRAFT_OUTPUT.
DRAFT_CONTENT = '{"draft": "e2e stub draft", "source": "stub-gateway"}'

WORKFLOW_DEFINITION: dict[str, Any] = {
    "key": WORKSPACE_ID,
    "label": "E2E 主流程（stub LLM）",
    "schema_version": 2,
    "nodes": {
        "_start": {
            "label": "入口",
            "type": "start",
            # The smoke drives the 粘贴 ID (ref) path with the seeded
            # cms-internal connection; no material storage is configured.
            "accepted_item_types": ["ref"],
        },
        "intake": {
            "label": "读取条目",
            "capability": "e2e_intake",
            "after": [],
            "inputs": [],
            "outputs": [INTAKE_OUTPUT],
        },
        "draft": {
            "label": "生成草稿",
            "capability": AGENT_CAPABILITY,
            "after": ["intake"],
            "inputs": [INTAKE_OUTPUT],
            "outputs": [DRAFT_OUTPUT],
            "execution": {"provider": "gateway", "model": "stub-model", "thinking": "low"},
        },
        "publish": {
            "label": "汇总",
            "capability": "e2e_publish",
            "after": ["draft"],
            "inputs": [INTAKE_OUTPUT, DRAFT_OUTPUT],
            "outputs": [PUBLISH_OUTPUT],
            "terminal": {"outcome": "published"},
        },
    },
    "edges": [
        {"from": "_start", "to": "intake"},
        {"from": "intake", "to": "draft"},
        {"from": "draft", "to": "publish"},
    ],
}

INTAKE_NODE_CODE = '''"""E2E main-flow intake node: record the ref item as the job input."""

from workspace_libs.node_sdk import NodeContext, entrypoint


@entrypoint
def run(ctx: NodeContext) -> None:
    ctx.checkpoint()
    out_path = ctx.artifacts.write_json(
        "intake_result.json",
        {
            "job_id": str(ctx.job.get("id", "")),
            "source_id": str(ctx.job.get("source_id") or ""),
            "marker": "e2e-main-flow-intake",
        },
    )
    ctx.logger.info("e2e intake: wrote %s", out_path.name)
'''

PUBLISH_NODE_CODE = '''"""E2E main-flow terminal node: aggregate intake + stub-Agent draft."""

from workspace_libs.node_sdk import NodeContext, entrypoint


@entrypoint
def run(ctx: NodeContext) -> None:
    ctx.checkpoint()
    intake = ctx.artifacts.read_json_object("intake_result.json")
    draft = ctx.artifacts.read_json_object("draft.json")
    out_path = ctx.artifacts.write_json(
        "publish_payload.json",
        {
            "job_id": str(ctx.job.get("id", "")),
            "intake": intake,
            "draft": draft,
            "simulated": True,
        },
    )
    ctx.logger.info("e2e publish: wrote %s", out_path.name)
'''

_SKILL_FILES = {
    "SKILL.md": "# E2E stub agent\n\nWrite the single required output file, then stop.\n",
    "references/output-contract.md": "Write draft.json (a JSON object) into the job directory.\n",
    "scripts/validate_output.py": "import sys; sys.exit(0)\n",
}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {result.stderr.strip()}")
    return result.stdout.strip()


def _ensure_skill_repo(repo: Path) -> str:
    """Create the skill git repo (seed-if-absent) and return its HEAD commit."""
    if not (repo / ".git").is_dir():
        repo.mkdir(parents=True, exist_ok=True)
        _git(repo, "init", "-b", SKILL_REF)
    for relpath, content in _SKILL_FILES.items():
        path = repo / relpath
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    # commit is a no-op when the tree is clean (idempotent reseed).
    _git(
        repo,
        "-c",
        "user.name=Agent Legion E2E",
        "-c",
        "user.email=e2e@localhost",
        "commit",
        "--allow-empty",
        "-m",
        "e2e stub agent skill",
    )
    return _git(repo, "rev-parse", f"{SKILL_REF}^{{commit}}")


def _lock_skill(dsn: str, repo: Path, commit: str) -> None:
    """Merge the e2e skill source + lock into the DB documents (seed-if-absent)."""
    store = SkillSourceStore(dsn)
    config = store.get_sources() or SkillsConfig()
    source = SkillSourceConfig(repo=str(repo), ref=SKILL_REF)
    if config.skills.get(SKILL_KEY) != source:
        config.skills[SKILL_KEY] = source
        store.put_sources(config)
    lock = store.get_lock() or SkillsLock()
    locked = LockedSkillSource(repo=str(repo), ref=SKILL_REF, commit=commit)
    if lock.skills.get(SKILL_KEY) != locked:
        lock.skills[SKILL_KEY] = locked
        lock.resolved_at = (
            datetime.datetime.now(datetime.UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        store.put_lock(lock)


def seed_main_flow_workspace(dsn: str, data_dir: Path) -> str:
    """Seed the hybrid-DAG workspace; returns its workspace id.

    Runs after the backend's first startup (schema exists, the startup
    pause-reset already happened) against a freshly reset e2e database, so
    the guards below are plain seed-if-absent, mirroring scripts/seed_demo.py.
    """
    job_db = JobQueries(dsn, jobs_dir=data_dir / "jobs")

    workspaces = [w for w in job_db.list_workspaces() if w.get("id") == WORKSPACE_ID]
    if not workspaces:
        job_db.create_workspace(
            WORKSPACE_NAME,
            default_workflow_key=WORKSPACE_ID,
            workspace_id=WORKSPACE_ID,
        )

    commit = _ensure_skill_repo(data_dir / "e2e-skill-repo")
    _lock_skill(dsn, data_dir / "e2e-skill-repo", commit)

    codes = NodeCodeService(dsn)
    seed_workspace_node_code(
        codes, WORKSPACE_ID, WORKSPACE_ID, "intake", INTAKE_NODE_CODE, "e2e seed"
    )
    seed_workspace_node_code(
        codes, WORKSPACE_ID, WORKSPACE_ID, "publish", PUBLISH_NODE_CODE, "e2e seed"
    )

    agents = AgentService(dsn, WORKSPACE_ID)
    if not agents.list_versions(AGENT_ID):
        agents.save_draft(
            AGENT_ID,
            AgentDefinition(capability=AGENT_CAPABILITY, runtime="velites", skill=SKILL_KEY),
            created_by="system",
        )
        agents.publish(AGENT_ID)

    # Publish after the Agent definition exists so the revision materializes
    # the draft node's Agent route (exactly one published Agent per capability).
    WorkflowRevisionService(job_db).ensure_active_revision(
        WORKSPACE_ID, workflow_definition_from_dict(WORKFLOW_DEFINITION)
    )

    # App startup resets every workspace to paused by design; the smoke needs
    # this workspace dispatching (the demo workspace stays paused).
    WorkspaceWorkerControl(dsn).resume(WORKSPACE_ID)
    logger.info("e2e main-flow workspace seeded: %s", WORKSPACE_ID)
    return WORKSPACE_ID
