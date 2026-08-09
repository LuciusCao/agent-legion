import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.jobs.queries import JobQueries
from server.app.main import create_app
from server.app.services.node_codes import NodeCodeService
from server.app.services.workflow_drafts import (
    validate_workflow_definition,
    validate_workflow_for_publish,
    workflow_definition_from_yaml_string,
)
from server.app.services.workflow_revision_format import (
    definition_hash,
    definition_to_yaml,
    serialize_definition,
    workflow_definition_to_response_payload,
)
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import (
    WorkflowDefinition,
    workflow_definition_from_dict,
    workflow_definition_from_mapping,
)
from tests.helpers import load_builtin_definition, replace_agent_catalog
from tests.helpers.auth import authenticate_client
from tests.postgres_support import TEST_DATABASE_URL


def test_publish_and_get_active_revision(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    definition = load_builtin_definition("question_comprehension_info")
    service = WorkflowRevisionService(queries)

    revision = service.publish_workspace_revision(workspace["id"], definition)
    active = service.get_active(workspace["id"], definition.key)

    assert active["id"] == revision["id"]
    assert active["workspace_id"] == workspace["id"]
    assert active["workflow_key"] == "question_comprehension_info"
    assert active["version"] == 1
    assert active["status"] == "active"
    assert active["definition_hash"]
    assert active["definition_json"]
    with queries._connect_read() as conn:
        route = conn.execute(
            "select target_kind, target_id from workspace_node_routes"
            " where workspace_id=%s and workflow_key=%s and node_key='generate_key_info'",
            (workspace["id"], definition.key),
        ).fetchone()
        capacity = conn.execute(
            "select max_concurrency, source_revision_id from workspace_node_capacities"
            " where workspace_id=%s and workflow_key=%s and node_key='generate_key_info'",
            (workspace["id"], definition.key),
        ).fetchone()
    assert route is not None
    assert dict(route) == {"target_kind": "agent", "target_id": "question-key-info-v1"}
    # Agent capacity is workspace-level now; publish no longer writes per-node rows.
    assert capacity is None


def test_runtime_only_save_updates_active_revision_without_new_version(
    tmp_path: Path,
) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("runtime-ws", default_workflow_key="runtime-flow")
    service = WorkflowRevisionService(queries)
    original = workflow_definition_from_mapping(
        {
            "key": "runtime-flow",
            "label": "Runtime Flow",
            "nodes": {
                "generate": {
                    "capability": "generate",
                    "outputs": ["result.json"],
                    "execution": {"provider": "gateway", "model": "old-model"},
                }
            },
        }
    )
    first = service.publish_workspace_revision(workspace["id"], original)
    updated = workflow_definition_from_mapping(
        {
            "key": "runtime-flow",
            "label": "Runtime Flow",
            "nodes": {
                "generate": {
                    "capability": "generate",
                    "outputs": ["result.json"],
                    "execution": {"provider": "gateway", "model": "new-model"},
                }
            },
        }
    )

    same = service.save_workspace_revision(workspace["id"], updated)

    assert same["id"] == first["id"]
    assert same["version"] == 1
    assert same["definition_hash"] != first["definition_hash"]
    assert '"new-model"' in same["definition_json"]

    structural = workflow_definition_from_mapping(
        {
            "key": "runtime-flow",
            "label": "Runtime Flow",
            "nodes": {
                "generate": {
                    "capability": "generate",
                    "outputs": ["result.json", "metadata.json"],
                }
            },
        }
    )
    second = service.save_workspace_revision(workspace["id"], structural)
    assert second["version"] == 2
    assert second["id"] != first["id"]


def _agent_nodes_definition(*, review_as_local: bool) -> WorkflowDefinition:
    # Agent routing is capability-driven: review_as_local gives the node a
    # capability no enabled Agent implements, so it keeps the handler path.
    review_capability = "local_review" if review_as_local else "review_key_info"
    return workflow_definition_from_mapping(
        {
            "key": "question_comprehension_info",
            "label": "QCI",
            "nodes": {
                "generate_key_info": {
                    "capability": "generate_key_info",
                },
                "review_key_info": {
                    "capability": review_capability,
                    "after": ["generate_key_info"],
                },
            },
        }
    )


def _route_and_capacity_rows(queries: JobQueries, workspace_id: str, workflow_key: str) -> dict:
    with queries._connect_read() as conn:
        routes = conn.execute(
            "select node_key, target_kind, target_id from workspace_node_routes"
            " where workspace_id=%s and workflow_key=%s",
            (workspace_id, workflow_key),
        ).fetchall()
        capacities = conn.execute(
            "select node_key, max_concurrency from workspace_node_capacities"
            " where workspace_id=%s and workflow_key=%s",
            (workspace_id, workflow_key),
        ).fetchall()
    return {
        "routes": {row["node_key"]: (row["target_kind"], row["target_id"]) for row in routes},
        "capacities": {row["node_key"]: row["max_concurrency"] for row in capacities},
    }


def test_republish_deletes_stale_agent_route_and_capacity_rows(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    service = WorkflowRevisionService(queries)
    service.publish_workspace_revision(
        workspace["id"], _agent_nodes_definition(review_as_local=False)
    )
    video_definition = load_builtin_definition("video_knowledge")
    service.publish_workspace_revision(workspace["id"], video_definition)
    # Legacy projection: a stale per-node capacity row must be pruned by the
    # next publish even though publish no longer writes such rows.
    with queries.connect() as conn:
        conn.execute(
            "insert into workspace_node_capacities(workspace_id, workflow_key, node_key, max_concurrency)"
            " values (%s, 'question_comprehension_info', 'generate_key_info', 20)",
            (workspace["id"],),
        )

    service.publish_workspace_revision(
        workspace["id"], _agent_nodes_definition(review_as_local=True)
    )

    question_rows = _route_and_capacity_rows(
        queries, workspace["id"], "question_comprehension_info"
    )
    assert question_rows["routes"] == {"generate_key_info": ("agent", "question-key-info-v1")}
    assert question_rows["capacities"] == {}
    video_rows = _route_and_capacity_rows(queries, workspace["id"], "video_knowledge")
    assert set(video_rows["routes"]) == {
        "subtitle_review",
        "chapter_generate",
        "interaction_generate",
        "content_review",
    }
    assert video_rows["capacities"] == {}


def test_reconcile_warns_and_skips_on_ambiguous_capability(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    service = WorkflowRevisionService(queries)
    service.publish_workspace_revision(
        workspace["id"], _agent_nodes_definition(review_as_local=False)
    )
    # Simulate catalog/DB desync: a second published definition for the same
    # capability. The DB partial unique index makes this unrepresentable via
    # real rows, so stub the catalog read (the reconcile guard is defense in
    # depth for catalogs produced before the index existed).
    from server.app.agent_catalog import AgentDefinition

    ambiguous = {
        "question-key-info-v1": AgentDefinition(
            capability="generate_key_info",
            runtime="velites",
            skill="question/generate_key_info",
        ),
        "question-key-info-v2": AgentDefinition(
            capability="generate_key_info",
            runtime="velites",
            skill="question/generate_key_info_v2",
        ),
    }
    monkeypatch.setattr(
        "server.app.services.workflow_revisions.published_agent_definitions",
        lambda _dsn: ambiguous,
    )

    with caplog.at_level(logging.WARNING, logger="server.app.services.workflow_revisions"):
        service.reconcile_active_agent_routes()

    warnings = [
        record
        for record in caplog.records
        if "Agent route migration skipped" in record.getMessage()
    ]
    # The bootstrap workspace carries an active revision for the same workflow,
    # so it warns too; what matters is that boot survives and our workspace warns.
    assert warnings
    assert any(workspace["id"] in record.getMessage() for record in warnings)
    # The ambiguous revision was skipped: its previously materialized rows are untouched.
    rows = _route_and_capacity_rows(queries, workspace["id"], "question_comprehension_info")
    assert rows["routes"]["generate_key_info"] == ("agent", "question-key-info-v1")


def test_reconcile_skips_and_keeps_routes_when_catalog_fully_disabled(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    service = WorkflowRevisionService(queries)
    service.publish_workspace_revision(
        workspace["id"], _agent_nodes_definition(review_as_local=False)
    )
    # Simulate the empty-catalog regression: every published definition archived.
    replace_agent_catalog({})

    with caplog.at_level(logging.WARNING, logger="server.app.services.workflow_revisions"):
        service.reconcile_active_agent_routes()

    assert any("no published Agent Definitions" in record.getMessage() for record in caplog.records)
    rows = _route_and_capacity_rows(queries, workspace["id"], "question_comprehension_info")
    assert rows["routes"]["generate_key_info"] == ("agent", "question-key-info-v1")


def test_reconcile_covers_active_revisions_beyond_default_workflow(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    service = WorkflowRevisionService(queries)
    service.publish_workspace_revision(
        workspace["id"], _agent_nodes_definition(review_as_local=False)
    )
    video_definition = load_builtin_definition("video_knowledge")
    service.publish_workspace_revision(workspace["id"], video_definition)
    # Simulate a pre-cutover active revision: no materialized routes/capacities.
    with queries.connect() as conn:
        conn.execute(
            "delete from workspace_node_routes where workspace_id=%s and workflow_key=%s",
            (workspace["id"], "video_knowledge"),
        )
        conn.execute(
            "delete from workspace_node_capacities where workspace_id=%s and workflow_key=%s",
            (workspace["id"], "video_knowledge"),
        )

    service.reconcile_active_agent_routes()

    video_rows = _route_and_capacity_rows(queries, workspace["id"], "video_knowledge")
    assert video_rows["routes"]["subtitle_review"] == ("agent", "video-subtitle-review-v1")
    assert video_rows["capacities"] == {}


def test_create_job_stores_workflow_revision_snapshot(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    definition = load_builtin_definition("question_comprehension_info")
    service = WorkflowRevisionService(queries)
    revision = service.publish_workspace_revision(workspace["id"], definition)

    job = queries.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id="Q1",
        batch_id="batch1",
        title="Question 1",
        node_keys=list(definition.nodes),
        workspace_id=workspace["id"],
        workflow_revision_id=revision["id"],
        workflow_version=revision["version"],
        workflow_definition_hash=revision["definition_hash"],
        workflow_definition_snapshot_json=revision["definition_json"],
    )

    assert job["workflow_revision_id"] == revision["id"]
    assert job["workflow_version"] == revision["version"]
    assert job["workflow_definition_hash"] == revision["definition_hash"]
    assert "fetch_questions" in job["workflow_definition_snapshot_json"]


def test_validate_workflow_definition_rejects_terminal_without_outcome(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        """
key: bad
label: Bad
schema_version: 2
nodes:
  a:
    label: A
    capability: a
    terminal: {}
edges: []
""",
        encoding="utf-8",
    )

    errors = validate_workflow_definition(path.read_text(encoding="utf-8"))

    assert any("terminal.outcome" in error for error in errors)


def test_publish_validation_reports_missing_executor_binding(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    definition = load_builtin_definition("question_comprehension_info")

    errors = validate_workflow_for_publish(
        definition=definition,
        workspace_id=workspace["id"],
        job_db=queries,
        settings_executor_definitions={},
    )

    assert any("executor binding" in error for error in errors)


def test_failed_publish_validation_preserves_active_revision(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws1", default_workflow_key="question_comprehension_info")
    definition = load_builtin_definition("question_comprehension_info")
    service = WorkflowRevisionService(queries)
    active = service.publish_workspace_revision(workspace["id"], definition)

    errors = validate_workflow_for_publish(
        definition=definition,
        workspace_id=workspace["id"],
        job_db=queries,
        settings_executor_definitions={},
    )

    assert errors
    assert (
        queries.get_active_workflow_revision(workspace["id"], definition.key)["id"] == active["id"]
    )


def test_get_active_workflow_revision_returns_definition_and_yaml(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as client:
        response = client.post(
            "/api/workspaces",
            json={"name": "Studio", "default_workflow_key": "question_comprehension_info"},
        )
        assert response.status_code == 200
        workspace_id = response.json()["workspace"]["id"]

        active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")

    assert active.status_code == 200
    payload = active.json()
    assert payload["revision"]["status"] == "active"
    assert payload["revision"]["version"] == 1
    assert payload["workflow"]["key"] == "question_comprehension_info"
    assert payload["workflow"]["nodes"]
    assert "key: question_comprehension_info" in payload["definition_yaml"]

    definition = workflow_definition_from_yaml_string(payload["definition_yaml"])
    assert definition.key == "question_comprehension_info"
    assert definition.nodes
    assert definition.edges


def test_get_workflow_revision_detail_returns_definition_and_yaml(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as client:
        response = client.post(
            "/api/workspaces",
            json={"name": "Studio", "default_workflow_key": "question_comprehension_info"},
        )
        assert response.status_code == 200
        workspace_id = response.json()["workspace"]["id"]

        active = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
        assert active.status_code == 200
        revision_id = active.json()["revision"]["id"]

        detail = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions/{revision_id}")

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["revision"]["id"] == revision_id
    assert payload["revision"]["status"] == "active"
    assert payload["workflow"]["key"] == "question_comprehension_info"
    assert payload["workflow"]["nodes"]
    assert "key: question_comprehension_info" in payload["definition_yaml"]


def test_get_workflow_revision_detail_returns_404_for_unknown_revision(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    workspace = app.state.job_db.create_workspace(
        "Studio",
        default_workflow_key="question_comprehension_info",
    )
    with authenticate_client(TestClient(app)) as client:
        response = client.get(f"/api/workspaces/{workspace['id']}/workflow-revisions/missing-rev")

    assert response.status_code == 404
    assert response.json()["detail"] == "Workflow revision not found"


def test_get_workflow_revision_detail_rejects_other_workspace_revision(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as client:
        first = client.post(
            "/api/workspaces",
            json={"name": "First", "default_workflow_key": "question_comprehension_info"},
        )
        second = client.post(
            "/api/workspaces",
            json={"name": "Second", "default_workflow_key": "question_comprehension_info"},
        )
        assert first.status_code == 200
        assert second.status_code == 200
        first_id = first.json()["workspace"]["id"]
        second_id = second.json()["workspace"]["id"]
        active = client.get(f"/api/workspaces/{first_id}/workflow-revisions/active")
        assert active.status_code == 200
        first_revision_id = active.json()["revision"]["id"]

        response = client.get(f"/api/workspaces/{second_id}/workflow-revisions/{first_revision_id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Workflow revision not found"


def test_get_active_workflow_revision_returns_404_for_workspace_without_revision(
    tmp_path: Path,
) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    workspace = app.state.job_db.create_workspace(
        "No Revision",
        default_workflow_key="question_comprehension_info",
    )
    with authenticate_client(TestClient(app)) as client:
        response = client.get(f"/api/workspaces/{workspace['id']}/workflow-revisions/active")

    assert response.status_code == 404
    assert response.json()["detail"] == "No active workflow revision"


def test_definition_to_yaml_upgrades_v1_to_schema_version_2(tmp_path: Path) -> None:
    definition = load_builtin_definition("video_knowledge")

    yaml_text = definition_to_yaml(definition)

    assert "schema_version: 2" in yaml_text
    parsed = workflow_definition_from_yaml_string(yaml_text)
    assert parsed.schema_version == 2
    assert parsed.edges


def test_response_payload_includes_terminal_outcome(tmp_path: Path) -> None:
    definition = load_builtin_definition("question_comprehension_info")

    payload = workflow_definition_to_response_payload(definition)

    terminal_nodes = [node for node in payload["nodes"] if node.get("terminal")]
    assert terminal_nodes
    assert all(node["terminal"]["outcome"] for node in terminal_nodes)


def test_publish_revision_records_node_code_pins(tmp_path: Path) -> None:
    """Publish snapshots published custom code versions as node_code_pins."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws-pins", default_workflow_key="question_comprehension_info"
    )
    codes = NodeCodeService(queries.path)
    codes.save_draft(
        workspace["id"],
        "question_comprehension_info",
        "fetch_questions",
        "def run(job, job_dir, runtime):\n    return None\n",
        "user:u1",
    )
    codes.publish(workspace["id"], "question_comprehension_info", "fetch_questions")
    definition = load_builtin_definition("question_comprehension_info")
    service = WorkflowRevisionService(queries)

    service.publish_workspace_revision(workspace["id"], definition)
    active = service.get_active(workspace["id"], definition.key)

    payload = json.loads(active["definition_json"])
    pins = payload["node_code_pins"]
    assert pins["fetch_questions"]["version"] == 1
    assert len(pins["fetch_questions"]["code_hash"]) == 64
    assert "clean_and_parse" not in pins
    # Pins are publish-moment state, not part of the definition: the hash
    # covers the pure definition only.
    assert active["definition_hash"] == definition_hash(serialize_definition(definition))
    # The definition round-trip ignores the sibling pins key.
    workflow_definition_from_dict(payload)


def test_publish_revision_without_custom_codes_has_no_pins(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws-no-pins", default_workflow_key="question_comprehension_info"
    )
    definition = load_builtin_definition("question_comprehension_info")
    service = WorkflowRevisionService(queries)

    service.publish_workspace_revision(workspace["id"], definition)
    active = service.get_active(workspace["id"], definition.key)

    assert "node_code_pins" not in json.loads(active["definition_json"])


def test_publish_revision_skips_pins_when_gate_disabled(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws-gated-pins", default_workflow_key="question_comprehension_info"
    )
    codes = NodeCodeService(queries.path)
    codes.save_draft(
        workspace["id"],
        "question_comprehension_info",
        "fetch_questions",
        "def run(job, job_dir, runtime):\n    return None\n",
        "user:u1",
    )
    codes.publish(workspace["id"], "question_comprehension_info", "fetch_questions")
    definition = load_builtin_definition("question_comprehension_info")
    service = WorkflowRevisionService(queries, custom_nodes_enabled=False)

    service.publish_workspace_revision(workspace["id"], definition)
    active = service.get_active(workspace["id"], definition.key)

    assert "node_code_pins" not in json.loads(active["definition_json"])


def test_runtime_only_update_preserves_node_code_pins(tmp_path: Path) -> None:
    """In-place (runtime settings only) revision updates keep node_code_pins."""
    from dataclasses import replace as dc_replace

    from server.app.workflows.schema import WorkflowNodeExecution

    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "ws-pins-keep", default_workflow_key="question_comprehension_info"
    )
    codes = NodeCodeService(queries.path)
    codes.save_draft(
        workspace["id"],
        "question_comprehension_info",
        "fetch_questions",
        "def run(job, job_dir, runtime):\n    return None\n",
        "user:u1",
    )
    codes.publish(workspace["id"], "question_comprehension_info", "fetch_questions")
    definition = load_builtin_definition("question_comprehension_info")
    service = WorkflowRevisionService(queries)
    service.publish_workspace_revision(workspace["id"], definition)

    # Runtime-only change: same structure, different execution settings.
    node = definition.nodes["generate_key_info"]
    updated = dc_replace(
        definition,
        nodes={
            **definition.nodes,
            "generate_key_info": dc_replace(
                node, execution=WorkflowNodeExecution(provider="deepseek", model="m2")
            ),
        },
    )
    service.save_workspace_revision(workspace["id"], updated)

    active = service.get_active(workspace["id"], definition.key)
    payload = json.loads(active["definition_json"])
    assert payload["node_code_pins"]["fetch_questions"]["version"] == 1
    # The runtime change did land, and no new revision was created.
    assert payload["nodes"]["generate_key_info"]["execution"]["model"] == "m2"
    assert active["version"] == 1
