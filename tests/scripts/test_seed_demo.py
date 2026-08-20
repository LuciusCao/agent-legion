from __future__ import annotations

import os
import subprocess
from pathlib import Path

from scripts.seed_demo import DEMO_WORKFLOW_KEY, seed_demo
from server.app.services.agent_service import published_agent_definitions
from server.app.services.skill_source_store import SkillSourceStore

REPO_ROOT = Path(__file__).resolve().parents[2]
IMPORT_SKILLS = REPO_ROOT / "scripts" / "import-demo.sh"


def _import_skills(target: Path) -> None:
    result = subprocess.run(
        ["bash", str(IMPORT_SKILLS)],
        capture_output=True,
        text=True,
        env={**os.environ, "AGENT_LEGION_DEMO_SKILLS_DIR": str(target)},
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_seed_demo_creates_complete_workspace_once(settings, job_db, tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    _import_skills(skills)

    first = seed_demo(settings, skill_root=skills)
    second = seed_demo(settings, skill_root=skills)

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

    store = SkillSourceStore(settings.database_url)
    sources = store.get_sources()
    lock = store.get_lock()
    assert sources is not None
    assert lock is not None
    for name in ("write-script", "review-script", "generate-questions", "review-questions"):
        key = f"education-video-problems-generation/{name}"
        assert sources.skills[key].repo == str((skills / name).resolve())
        assert lock.skills[key].commit
    assert second.sources_added == 0
    assert second.locks_updated == 0
    assert second.node_codes_added == 0
    assert second.agents_added == 0


def test_seed_demo_reuses_existing_bound_workspace(settings, job_db, tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    _import_skills(skills)
    existing = job_db.create_workspace(
        "Existing Demo",
        default_workflow_key=DEMO_WORKFLOW_KEY,
    )

    result = seed_demo(settings, skill_root=skills)

    assert result.workspace_created is False
    assert result.workspace_id == existing["id"]
    assert job_db.get_active_workflow_revision(existing["id"], DEMO_WORKFLOW_KEY) is not None
