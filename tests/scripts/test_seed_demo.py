from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts.seed_demo import DEMO_WORKFLOW_KEY, seed_demo
from server.app.services.agent_service import published_agent_definitions
from server.app.services.node_codes import NodeCodeService
from server.app.services.skill_lock_store import SkillLockStore

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


def test_seed_demo_creates_complete_workspace_once(
    settings, job_db, tmp_path: Path, demo_skills_home: Path
) -> None:
    first = seed_demo(settings)
    second = seed_demo(settings)

    assert first.workspace_created is True
    assert second.workspace_created is False
    assert second.workspace_id == first.workspace_id
    matching = [
        workspace
        for workspace in job_db.list_workspaces()
        if workspace["default_workflow_key"] == DEMO_WORKFLOW_KEY
    ]
    assert [workspace["id"] for workspace in matching] == [first.workspace_id]
    assert job_db.get_active_workflow_revision(first.workspace_id, DEMO_WORKFLOW_KEY) is not None
    assert len(published_agent_definitions(settings.database_url, first.workspace_id)) == 4
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
    assert second.agents_added == 0


def test_seed_demo_reuses_existing_bound_workspace(
    settings, job_db, tmp_path: Path, demo_skills_home: Path
) -> None:
    existing = job_db.create_workspace(
        "Existing Demo",
        default_workflow_key=DEMO_WORKFLOW_KEY,
    )

    result = seed_demo(settings)

    assert result.workspace_created is False
    assert result.workspace_id == existing["id"]
    assert job_db.get_active_workflow_revision(existing["id"], DEMO_WORKFLOW_KEY) is not None


def test_seed_demo_establishes_workspace_before_injecting_assets(
    settings, job_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No skills imported under this HOME: the lock step fails loudly, after
    # the workspace already exists (asset injection never half-applies).
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    with pytest.raises(RuntimeError, match="demo skill repo is missing"):
        seed_demo(settings)

    matching = [
        workspace
        for workspace in job_db.list_workspaces()
        if workspace["default_workflow_key"] == DEMO_WORKFLOW_KEY
    ]
    assert len(matching) == 1
    assert job_db.get_active_workflow_revision(matching[0]["id"], DEMO_WORKFLOW_KEY) is None
