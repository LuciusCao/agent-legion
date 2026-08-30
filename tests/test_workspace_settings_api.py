from pathlib import Path

from fastapi.testclient import TestClient

from server.app.agent_catalog import AgentDefinition
from server.app.main import create_app
from server.app.services.agent_service import AgentService
from tests.helpers import publish_builtin_revision, seed_workspace_agent_definitions
from tests.helpers.auth import authenticate_client
from tests.postgres_support import TEST_DATABASE_URL


def test_workspace_settings_round_trip(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws = c.post(
            "/api/workspaces",
            json={"id": "education_video_problems_generation", "name": "test_ws"},
        )
        assert ws.status_code == 200
        workspace_id = ws.json()["workspace"]["id"]
        # Schema v62: creation seeds nothing; publish the demo DAG so the
        # node-level settings below have a workflow to resolve against.
        publish_builtin_revision(c.app.state.job_db, workspace_id)
        connection = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={
                "nodeConfig": {"intake_knowledge_points": {"timeout_seconds": 120}},
            },
        )
        intake = c.patch(
            f"/api/workspaces/{workspace_id}/settings/intake",
            json={
                "entityType": "video",
            },
        )
        workflow = c.patch(
            f"/api/workspaces/{workspace_id}/settings/workflow",
            json={"workflowKey": "education_video_problems_generation"},
        )
        fetched = c.get(f"/api/workspaces/{workspace_id}/settings")

    assert connection.status_code == 200
    assert intake.status_code == 200
    assert workflow.status_code == 200
    settings = fetched.json()["settings"]
    assert "cmsUrl" not in settings
    assert "cmsToken" not in settings
    assert "resources" not in settings
    assert settings["nodeConfig"]["intake_knowledge_points"]["timeout_seconds"] == 120
    assert settings["entityType"] == "video"
    # intakeModes/labelOverrides/agentDefaults retired at schema v64.
    assert "intakeModes" not in settings
    assert "labelOverrides" not in settings
    assert "agentDefaults" not in settings
    assert settings["workflowKey"] == "education_video_problems_generation"
    workspace = app.state.job_db.get_workspace(workspace_id)
    assert "pipeline_config" not in workspace


def test_workspace_settings_workflow_rejects_legacy_concurrency_fields(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws = c.post(
            "/api/workspaces",
            json={"id": "education_video_problems_generation", "name": "test_ws"},
        )
        assert ws.status_code == 200
        workspace_id = ws.json()["workspace"]["id"]
        response = c.patch(
            f"/api/workspaces/{workspace_id}/settings/workflow",
            json={
                "workflowKey": "education_video_problems_generation",
                "localConcurrency": 5,
                "agentConcurrency": 3,
                "nodeLocalConcurrency": {"intake_knowledge_points": 2},
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    legacy_fields = {"localConcurrency", "agentConcurrency", "nodeLocalConcurrency"}
    extra_fields = {
        e["loc"][-1] for e in detail if e.get("type") == "extra_forbidden" and e.get("loc")
    }
    assert extra_fields == legacy_fields
    workspace = app.state.job_db.get_workspace(workspace_id)
    assert "pipeline_config" not in workspace


def test_workflow_openapi_contract_is_capability_only(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    schemas = app.openapi()["components"]["schemas"]

    node = schemas["WorkflowNodeResponse"]["properties"]
    detail = schemas["WorkflowDefinitionResponse"]["properties"]

    assert "capability" in node
    assert "runner" not in node
    assert "agent" not in node
    assert "concurrency" not in detail


def test_lists_workspace_workflow_revisions(client):
    response = client.post(
        "/api/workspaces",
        json={"id": "education_video_problems_generation", "name": "Workflow Studio"},
    )
    workspace_id = response.json()["workspace"]["id"]

    revisions = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions")

    assert revisions.status_code == 200
    payload = revisions.json()
    assert "revisions" in payload


def test_workspace_settings_agent_defaults_section_retired(client):
    # Schema v64: workspace-level Agent defaults are retired — the section
    # endpoint no longer exists and the payload carries no agentDefaults.
    response = client.post(
        "/api/workspaces",
        json={"id": "education_video_problems_generation", "name": "agent_defaults_ws"},
    )
    workspace_id = response.json()["workspace"]["id"]

    fetched = client.get(f"/api/workspaces/{workspace_id}/settings")
    assert fetched.status_code == 200
    assert "agentDefaults" not in fetched.json()["settings"]

    saved = client.patch(
        f"/api/workspaces/{workspace_id}/settings/agent-defaults",
        json={"agentDefaults": {"provider": "deepseek", "model": "m"}},
    )
    assert saved.status_code == 422


def _inject_write_script_config_schema(workspace_id: str) -> None:
    """Publish a new example-write-script-v1 version carrying a config_schema.

    Agent definitions are workspace-scoped (schema v46): the workspace already
    holds the seeded demo agent (workspaces binding the demo workflow get the
    factory templates at binding time), so this publishes v2 inside it.
    """
    schema = {
        "type": "object",
        "properties": {
            "max_items": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100}
        },
    }
    service = AgentService(TEST_DATABASE_URL, workspace_id)
    entity = service.get_published("example-write-script-v1")
    assert entity is not None
    updated = AgentDefinition.model_validate(entity.definition).model_copy(
        update={"config_schema": schema}
    )
    service.save_draft("example-write-script-v1", updated, "user:test")
    service.publish("example-write-script-v1")


def test_workspace_settings_nodes_round_trip(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws = c.post(
            "/api/workspaces",
            json={"id": "education_video_problems_generation", "name": "nodes_ws"},
        )
        assert ws.status_code == 200
        workspace_id = ws.json()["workspace"]["id"]
        publish_builtin_revision(c.app.state.job_db, workspace_id)
        seed_workspace_agent_definitions(workspace_id)
        _inject_write_script_config_schema(workspace_id)

        fetched = c.get(f"/api/workspaces/{workspace_id}/settings")
        assert fetched.status_code == 200
        settings = fetched.json()["settings"]
        assert settings["nodeConfig"] == {}
        # write_script comes from the Agent catalog; intake_knowledge_points
        # is an executor capability whose schema is declared in the built-in
        # executor definitions (D15). publish_content declares no parameters,
        # but as a code-routed node it still carries the platform-reserved
        # execution keys (timeout_seconds/sandbox_network, P-0.5 step 1).
        assert set(settings["nodeConfigSchemas"]) == {
            "write_script",
            "intake_knowledge_points",
            "publish_content",
        }

        saved = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"write_script": {"max_items": 5}}},
        )
        assert saved.status_code == 200
        assert saved.json()["settings"]["nodeConfig"] == {"write_script": {"max_items": 5}}

        saved_executor = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"intake_knowledge_points": {"timeout_seconds": 120}}},
        )
        assert saved_executor.status_code == 200
        assert saved_executor.json()["settings"]["nodeConfig"] == {
            "write_script": {"max_items": 5},
            "intake_knowledge_points": {"timeout_seconds": 120},
        }

        cleared = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"write_script": {}, "intake_knowledge_points": {}}},
        )
        assert cleared.status_code == 200
        assert cleared.json()["settings"]["nodeConfig"] == {}

    workspace = app.state.job_db.get_workspace(workspace_id)
    assert workspace["node_config"] == {"education_video_problems_generation": {}}


def test_workspace_settings_nodes_reject_invalid_overrides(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws = c.post(
            "/api/workspaces",
            json={"id": "education_video_problems_generation", "name": "nodes_ws_bad"},
        )
        workspace_id = ws.json()["workspace"]["id"]
        publish_builtin_revision(c.app.state.job_db, workspace_id)
        seed_workspace_agent_definitions(workspace_id)
        _inject_write_script_config_schema(workspace_id)

        unknown_key = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"write_script": {"nope": 1}}},
        )
        out_of_bounds = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"write_script": {"max_items": 0}}},
        )
        executor_unknown_key = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"intake_knowledge_points": {"max_items": 5}}},
        )
        unknown_node = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"not_a_node": {"max_items": 5}}},
        )

    assert unknown_key.status_code == 400
    assert out_of_bounds.status_code == 400
    # intake_knowledge_points is an executor node with a declared schema (D15);
    # max_items is not part of it.
    assert executor_unknown_key.status_code == 400
    assert unknown_node.status_code == 400


def test_workspace_settings_node_config_is_schema_validated(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws = c.post(
            "/api/workspaces",
            json={"id": "education_video_problems_generation", "name": "res_ws"},
        )
        workspace_id = ws.json()["workspace"]["id"]
        publish_builtin_revision(c.app.state.job_db, workspace_id)

        fetched = c.get(f"/api/workspaces/{workspace_id}/settings")
        assert fetched.status_code == 200
        node_schemas = fetched.json()["settings"]["nodeConfigSchemas"]
        # The intake node declares no tunables of its own (materials-and-runs
        # §9 removed knowledge_dir); the platform execution keys are merged
        # into every code node's effective schema (default 600 here).
        timeout = node_schemas["intake_knowledge_points"]["properties"]["timeout_seconds"]
        assert timeout["default"] == 600

        bad_type = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"intake_knowledge_points": {"timeout_seconds": "fast"}}},
        )
        unknown_key = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"intake_knowledge_points": {"evil": 1}}},
        )
        ok = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"intake_knowledge_points": {"timeout_seconds": 120}}},
        )

    assert bad_type.status_code == 400
    assert unknown_key.status_code == 400
    assert ok.status_code == 200
    assert ok.json()["settings"]["nodeConfig"]["intake_knowledge_points"] == {
        "timeout_seconds": 120
    }


def test_node_override_validation_uses_workspace_active_revision(tmp_path):
    """#112 incident regression: override validation must read the workspace's
    ACTIVE revision, not a stale global template. After v2 drops a node, an
    override for it is rejected even though the v1 DAG (and the retired
    catalog template) still carried it."""
    from dataclasses import replace

    from server.app.services.workflow_revisions import WorkflowRevisionService
    from tests.helpers import load_builtin_definition

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws = c.post(
            "/api/workspaces",
            json={"id": "education_video_problems_generation", "name": "rev_ws"},
        )
        assert ws.status_code == 200
        workspace_id = ws.json()["workspace"]["id"]

        # Publish v1 (the demo DAG), then v2: the demo DAG minus
        # review_questions (and its edges).
        definition = load_builtin_definition("education_video_problems_generation")
        WorkflowRevisionService(app.state.job_db).publish_workspace_revision(
            workspace_id, definition
        )
        nodes = {
            key: replace(node, after=[a for a in node.after if a != "review_questions"])
            for key, node in definition.nodes.items()
            if key != "review_questions"
        }
        edges = [
            edge
            for edge in definition.edges
            if "review_questions" not in (edge.source, edge.target)
        ]
        v2 = replace(definition, nodes=nodes, edges=edges)
        revision = WorkflowRevisionService(app.state.job_db).publish_workspace_revision(
            workspace_id, v2
        )
        assert revision["version"] == 2

        removed = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"review_questions": {}}},
        )
        surviving = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"intake_knowledge_points": {"timeout_seconds": 120}}},
        )

    assert removed.status_code == 400
    assert "Unknown node" in removed.json()["detail"]
    assert surviving.status_code == 200


def test_blank_workspace_first_publish_adopts_key_and_runs_job(tmp_path):
    """Full catalog-free flow (#112 acceptance, v62 semantics): create a
    workspace with an explicit id (which IS the workflow key), publish a
    draft whose key matches, then intake a job. A mismatched draft key is
    rejected with 422 — the key is immutable."""
    from server.app.services.node_codes import NodeCodeService

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws = c.post(
            "/api/workspaces",
            json={"id": "acme_flow", "name": "acme_flow"},
        )
        assert ws.status_code == 200, ws.text
        workspace = ws.json()["workspace"]
        workspace_id = workspace["id"]
        # Schema v62: the key is bound at creation (id == key), not adopted.
        assert workspace["default_workflow_key"] == "acme_flow"

        # The code node needs published code before the first revision (the
        # known bootstrap constraint; node code is workspace-scoped).
        codes = NodeCodeService(app.state.job_db.dsn_identity)
        codes.save_draft(
            workspace_id,
            "acme_flow",
            "fetch",
            "def run(job, job_dir, runtime):\n    pass\n",
            "test seed",
        )
        codes.publish(workspace_id, "acme_flow", "fetch")

        draft_yaml = (
            "key: acme_flow\n"
            "label: Acme Flow\n"
            "schema_version: 2\n"
            "intake:\n"
            "  modes:\n"
            "    direct_ids:\n"
            "      label: Direct IDs\n"
            "      input_field: question_ids\n"
            "nodes:\n"
            "  fetch:\n"
            "    label: Fetch\n"
            "    capability: fetch\n"
            "    outputs: [fetch.json]\n"
            "edges: []\n"
        )
        published = c.post(
            f"/api/workspaces/{workspace_id}/workflow-drafts/publish",
            json={"definition_yaml": draft_yaml},
        )
        assert published.status_code == 200, published.text
        assert published.json()["valid"] is True

        # A draft key that does not match the bound key is rejected (422),
        # even before any revision exists.
        mismatched = c.post(
            f"/api/workspaces/{workspace_id}/workflow-drafts/publish",
            json={"definition_yaml": draft_yaml.replace("key: acme_flow", "key: typo_flow")},
        )
        assert mismatched.status_code == 422

        fetched = c.get(f"/api/workspaces/{workspace_id}")
        assert fetched.json()["workspace"]["default_workflow_key"] == "acme_flow"
        active = c.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
        assert active.status_code == 200
        assert active.json()["revision"]["version"] == 1

        batch = c.post(
            f"/api/workspaces/{workspace_id}/job-batches",
            json={
                "workflow_key": "acme_flow",
                "source_kind": "direct_ids",
                "question_ids": ["Q1"],
            },
        )

    assert batch.status_code == 200, batch.text
    assert batch.json()["created_count"] == 1
