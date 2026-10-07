"""The seeded demo workflow republishes through the #935 gate unchanged.

Mirrors the e2e helper ``widenDemoWorkflowItemTypes`` (frontend/e2e/helpers.ts):
a fresh demo workspace's active revision is read back as YAML and published
again through ``/workflow-drafts/publish``. Since the #440 P3 gate flip every
agent node must carry its own execution profile, so the built-in demo
template must ship one (CI e2e-smoke regression on PR #1080).
"""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from server.app.main import create_app
from server.app.workflows.builtin_demo import DEMO_WORKFLOW_KEY
from tests.helpers import publish_builtin_revision
from tests.helpers.auth import authenticate_client


def test_fresh_demo_workspace_active_workflow_republishes_through_the_gate(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The #322 skill-repo check needs the imported demo skill repos on disk
    # (make import-demo); this test pins the profile gate, not the repos.
    monkeypatch.setattr(
        "server.app.services.workflow_draft_publish.skill_repo_publish_errors",
        lambda *args, **kwargs: [],
    )
    app = create_app(data_dir=tmp_path, start_worker=False)
    job_db = app.state.job_db
    # App startup may already provision the demo workspace (seed-if-absent).
    if job_db.get_workspace(DEMO_WORKFLOW_KEY) is None:
        job_db.create_workspace("Demo", workspace_id=DEMO_WORKFLOW_KEY)
    publish_builtin_revision(job_db, DEMO_WORKFLOW_KEY)

    with authenticate_client(TestClient(app)) as client:
        active = client.get(f"/api/workspaces/{DEMO_WORKFLOW_KEY}/workflow-revisions/active")
        assert active.status_code == 200, active.text
        definition = yaml.safe_load(active.json()["definition_yaml"])
        start = next(n for n in definition["nodes"].values() if n.get("type") == "start")
        start["accepted_item_types"] = ["material", "ref"]
        published = client.post(
            f"/api/workspaces/{DEMO_WORKFLOW_KEY}/workflow-drafts/publish",
            json={"definition_yaml": yaml.safe_dump(definition, allow_unicode=True)},
        )

    assert published.status_code == 200, published.text
    assert published.json()["errors"] == []
    assert published.json()["valid"] is True
