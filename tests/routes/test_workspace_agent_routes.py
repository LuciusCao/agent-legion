import json
from pathlib import Path

from fastapi.testclient import TestClient

from server.app.db.migrations.agent_profile_backfill import migrate_agent_profile_backfill
from server.app.db.transaction import read_connection, write_transaction
from server.app.jobs.queries import JobQueries
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.helpers import load_builtin_definition, seed_workspace_agent_definitions
from tests.helpers.node_profile import legacy_profile_variant
from tests.postgres_support import TEST_DATABASE_URL


def _publish(client: TestClient, workspace_id: str = "ws-routes") -> None:
    job_db = JobQueries(TEST_DATABASE_URL, Path(client.app.state.settings.jobs_dir))
    job_db.create_workspace(workspace_id)
    # Agent definitions are workspace-scoped (schema v46): seed the demo
    # agents into this workspace before publishing so routes materialize.
    seed_workspace_agent_definitions(workspace_id)
    # #935: the demo ships self-contained nodes (no routes); this endpoint
    # reports the legacy materialized routes, so publish the legacy variant.
    definition = legacy_profile_variant(
        load_builtin_definition("education_video_problems_generation")
    )
    WorkflowRevisionService(job_db).publish_workspace_revision(workspace_id, definition)


def test_agent_routes_returns_materialized_routes(client: TestClient) -> None:
    _publish(client)

    resp = client.get("/api/workspaces/ws-routes/agent-routes")

    assert resp.status_code == 200
    routes = resp.json()["routes"]
    by_node = {entry["node_key"]: entry for entry in routes}
    assert set(by_node) == {
        "write_script",
        "review_script",
        "generate_questions",
        "review_questions",
    }
    entry = by_node["write_script"]
    # #211 M3: the route entry no longer carries a workflow_key.
    assert "workflow_key" not in entry
    assert entry["agent_id"] == "example-write-script-v1"
    assert entry["capability"] == "write_script"
    # issue #76: skill 绑定迁到 DAG 节点，Agent 定义的 legacy 兜底为空。
    assert entry["agent_skill"] == ""
    assert entry["node_label"]


def test_agent_routes_empty_without_published_revision(client: TestClient) -> None:
    resp = client.get("/api/workspaces/unknown-ws/agent-routes")

    assert resp.status_code == 200
    assert resp.json() == {"routes": []}


def _inline_active_revision() -> dict:
    """Run the v93 backfill against the published revision (frozen routes stay)."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        migrate_agent_profile_backfill(conn)
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select definition_json from workflow_revisions"
            " where workspace_id='ws-routes' and status='active'"
        ).fetchone()
    return json.loads(row["definition_json"])


def test_agent_routes_hide_frozen_rows_of_self_contained_nodes(client: TestClient) -> None:
    """#1079（#440 P3b）：v93 内联后路由行冻结不删，设置页不再展示其映射。"""
    _publish(client)
    payload = _inline_active_revision()
    provenance = payload.get("agent_profile_provenance") or {}
    assert provenance, "the backfill inlines at least one demo agent node"

    with read_connection(TEST_DATABASE_URL) as conn:
        frozen = {
            row["node_key"]
            for row in conn.execute(
                "select node_key from workspace_node_routes where workspace_id='ws-routes'"
            ).fetchall()
        }
    assert set(provenance) <= frozen  # rows stay for old snapshots

    resp = client.get("/api/workspaces/ws-routes/agent-routes")

    assert resp.status_code == 200
    shown = {entry["node_key"] for entry in resp.json()["routes"]}
    assert shown.isdisjoint(provenance)
    # Nodes the backfill could not inline keep their (still live) route.
    assert shown == frozen - set(provenance)


def test_agent_provenance_lists_inlined_nodes(client: TestClient) -> None:
    """#1079（#440 D1）：「已内联到 N 个节点」读 active revision 的 provenance。"""
    _publish(client)
    payload = _inline_active_revision()
    provenance = payload["agent_profile_provenance"]

    resp = client.get("/api/workspaces/ws-routes/agent-provenance")

    assert resp.status_code == 200
    nodes = resp.json()["nodes"]
    assert [entry["node_key"] for entry in nodes] == sorted(provenance)
    by_node = {entry["node_key"]: entry for entry in nodes}
    for node_key, entry in provenance.items():
        assert by_node[node_key]["agent_id"] == entry["agent_id"]
        assert by_node[node_key]["agent_version"] == entry["version"]
        assert by_node[node_key]["node_label"] == payload["nodes"][node_key]["label"]


def test_agent_provenance_empty_without_backfill(client: TestClient) -> None:
    _publish(client)
    assert client.get("/api/workspaces/ws-routes/agent-provenance").json() == {"nodes": []}
    assert client.get("/api/workspaces/unknown-ws/agent-provenance").json() == {"nodes": []}
