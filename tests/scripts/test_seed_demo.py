from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.seed_demo import DEMO_WORKFLOW_KEY, seed_demo
from server.app.agent_catalog import AgentDefinition
from server.app.agent_catalog.builtin import BUILTIN_AGENT_DEFINITIONS
from server.app.db.transaction import read_connection
from server.app.services.agent_node_profile import resolve_agent_node_profile
from server.app.services.node_codes import NodeCodeService
from server.app.services.skill_lock_store import SkillLockStore
from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
from server.app.workflows.definition import workflow_definition_from_dict
from tests.helpers import publish_agent_definition, save_agent_definition_draft

REPO_ROOT = Path(__file__).resolve().parents[2]
IMPORT_SKILLS = REPO_ROOT / "scripts" / "import-demo.sh"


@pytest.fixture
def demo_skills_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Import the demo skills in place under a fake HOME's skills root (#322:
    dispatch and the seed resolve repos at <skills root>/<key> only)."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    result = subprocess.run(
        ["bash", str(IMPORT_SKILLS)],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return home / ".agents" / "skills" / "education-video-problems-generation"


def _agent_rows(settings, workspace_id: str) -> list[dict]:
    with read_connection(settings.database_url) as conn:
        rows = conn.execute(
            "select entity_key, version, status, definition_json from versioned_entities "
            "where entity_type='agent' and workspace_id=%s order by entity_key, version",
            (workspace_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _assert_demo_profiles(revision: dict) -> None:
    definition = workflow_definition_from_dict(json.loads(str(revision["definition_json"])))
    assert definition.execution.runtime == "velites"
    assert DEMO_WORKFLOW_DEFINITION["execution"] == {"runtime": "velites"}
    expected = {
        "write_script": ("example-write-script-v1", "write-script"),
        "review_script": ("example-review-script-v1", "review-script"),
        "generate_questions": ("example-generate-questions-v1", "generate-questions"),
        "review_questions": ("example-review-questions-v1", "review-questions"),
    }
    for node_key, (agent_id, skill_name) in expected.items():
        assert DEMO_WORKFLOW_DEFINITION["nodes"][node_key]["execution"] == {"runtime": "velites"}
        node = definition.nodes[node_key]
        assert node.execution.runtime == "velites"
        assert node.skill is not None
        assert node.skill.key == f"education-video-problems-generation/{skill_name}"
        assert node.skill.ref == "v1.0.0"
        profile = resolve_agent_node_profile(node, {})
        assert profile is not None
        assert profile.source == "node"
        assert profile.legacy_ref is None
        assert profile.dispatch_definition == BUILTIN_AGENT_DEFINITIONS[agent_id]


def test_seed_demo_creates_complete_workspace_once(
    settings, job_db, tmp_path: Path, demo_skills_home: Path
) -> None:
    first = seed_demo(settings)
    first_revision = job_db.get_active_workflow_revision(first.workspace_id, DEMO_WORKFLOW_KEY)
    assert first_revision is not None
    second = seed_demo(settings)
    second_revision = job_db.get_active_workflow_revision(first.workspace_id, DEMO_WORKFLOW_KEY)

    assert first.workspace_created is True
    assert second.workspace_created is False
    assert second.workspace_id == first.workspace_id
    matching = [
        workspace for workspace in job_db.list_workspaces() if workspace["id"] == DEMO_WORKFLOW_KEY
    ]
    assert [workspace["id"] for workspace in matching] == [first.workspace_id]
    assert second_revision is not None
    assert second_revision["id"] == first_revision["id"]
    assert second_revision["version"] == first_revision["version"] == 1
    assert _agent_rows(settings, first.workspace_id) == []
    _assert_demo_profiles(second_revision)
    codes = NodeCodeService(settings.database_url)
    for node_key in ("intake_knowledge_points", "publish_content"):
        assert codes.get_effective_code(first.workspace_id, DEMO_WORKFLOW_KEY, node_key) is not None
        assert codes.get_global_published(DEMO_WORKFLOW_KEY, node_key) is None

    store = SkillLockStore(settings.database_url)
    lock = store.get_lock()
    assert lock is not None
    for name in ("write-script", "review-script", "generate-questions", "review-questions"):
        key = f"education-video-problems-generation/{name}"
        repo = demo_skills_home / name
        commit = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "v1.0.0^{commit}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert lock.skills[key].refs["v1.0.0"] == commit
    assert first.locks_updated == 4
    assert second.locks_updated == 0
    assert second.node_codes_added == 0


def test_seed_demo_reuses_existing_bound_workspace(
    settings, job_db, tmp_path: Path, demo_skills_home: Path
) -> None:
    existing = job_db.create_workspace("Existing Demo", workspace_id=DEMO_WORKFLOW_KEY)
    saved = save_agent_definition_draft(
        settings.database_url,
        existing["id"],
        "legacy-history",
        AgentDefinition(capability="legacy_history", runtime="pi", tools=("read",)),
    )
    publish_agent_definition(
        settings.database_url, existing["id"], "legacy-history", saved.definition_hash
    )
    history_before = _agent_rows(settings, existing["id"])

    result = seed_demo(settings)

    assert result.workspace_created is False
    assert result.workspace_id == existing["id"]
    active = job_db.get_active_workflow_revision(existing["id"], DEMO_WORKFLOW_KEY)
    assert active is not None
    _assert_demo_profiles(active)
    assert len(history_before) == 1
    assert _agent_rows(settings, existing["id"]) == history_before


def test_seed_demo_establishes_workspace_before_injecting_assets(
    settings, job_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No skills imported under this HOME: the lock step fails loudly, after
    # the workspace already exists (asset injection never half-applies).
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    with pytest.raises(RuntimeError, match="demo skill repo is missing"):
        seed_demo(settings)

    matching = [
        workspace for workspace in job_db.list_workspaces() if workspace["id"] == DEMO_WORKFLOW_KEY
    ]
    assert len(matching) == 1
    assert job_db.get_active_workflow_revision(matching[0]["id"], DEMO_WORKFLOW_KEY) is None
