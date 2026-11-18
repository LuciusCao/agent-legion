"""Revision publication's frozen Agent-route retention and prune contracts."""

from pathlib import Path

import pytest

from server.app.jobs.queries import JobQueries
from server.app.services.workflow_drafts import validate_workflow_for_publish
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import (
    WorkflowDefinition,
    workflow_definition_from_mapping,
)
from tests.helpers import load_builtin_definition, replace_agent_catalog
from tests.helpers.legacy_agent_definitions import seed_workspace_agent_definitions
from tests.helpers.node_profile import legacy_profile_variant
from tests.postgres_support import TEST_DATABASE_URL


def test_publish_and_get_active_revision(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws1")
    seed_workspace_agent_definitions(workspace["id"])
    definition = legacy_profile_variant(
        load_builtin_definition("education_video_problems_generation")
    )
    service = WorkflowRevisionService(queries)

    revision = service.publish_workspace_revision(workspace["id"], definition)
    active = service.get_active(workspace["id"], definition.key)

    assert active["id"] == revision["id"]
    assert active["workspace_id"] == workspace["id"]
    assert active["version"] == 1
    assert active["status"] == "active"
    assert active["definition_hash"]
    assert active["definition_json"]
    with queries._connect_read() as conn:
        route = conn.execute(
            "select target_kind, target_id from workspace_node_routes"
            " where workspace_id=%s and node_key='write_script'",
            (workspace["id"],),
        ).fetchone()
        capacity = conn.execute(
            "select max_concurrency, source_revision_id from workspace_node_capacities"
            " where workspace_id=%s and node_key='write_script'",
            (workspace["id"],),
        ).fetchone()
    # Even an internal legacy publish with a unique catalog match writes no route.
    assert route is None
    assert capacity is None


def _agent_nodes_definition(*, review_as_local: bool) -> WorkflowDefinition:
    # Explicit node types (#284): write_script always runs as an Agent node;
    # review_as_local flips the review node to a code node with a capability
    # no published Agent implements.
    return workflow_definition_from_mapping(
        {
            "key": "agent_nodes_flow",
            "label": "Agent Nodes Flow",
            "nodes": {
                "write_script": {
                    "type": "agent",
                    "capability": "write_script",
                },
                "review_script": {
                    "type": "code" if review_as_local else "agent",
                    "capability": "local_review" if review_as_local else "review_script",
                    "after": ["write_script"],
                },
            },
        }
    )


def _route_and_capacity_rows(queries: JobQueries, workspace_id: str) -> dict:
    with queries._connect_read() as conn:
        routes = conn.execute(
            "select node_key, target_kind, target_id from workspace_node_routes"
            " where workspace_id=%s",
            (workspace_id,),
        ).fetchall()
        capacities = conn.execute(
            "select node_key, max_concurrency from workspace_node_capacities where workspace_id=%s",
            (workspace_id,),
        ).fetchall()
    return {
        "routes": {row["node_key"]: (row["target_kind"], row["target_id"]) for row in routes},
        "capacities": {row["node_key"]: row["max_concurrency"] for row in capacities},
    }


def test_republish_prunes_only_deleted_or_code_agent_routes(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws1")
    seed_workspace_agent_definitions(workspace["id"])
    with queries.connect() as conn:
        for node_key in ("write_script", "review_script", "removed_node"):
            conn.execute(
                "insert into workspace_node_routes(workspace_id, node_key, target_kind, target_id)"
                " values (%s, %s, 'agent', %s)",
                (workspace["id"], node_key, f"historical-{node_key}"),
            )
        conn.execute(
            "insert into workspace_node_capacities(workspace_id, node_key, max_concurrency)"
            " values (%s, 'write_script', 20)",
            (workspace["id"],),
        )

    WorkflowRevisionService(queries).publish_workspace_revision(
        workspace["id"], _agent_nodes_definition(review_as_local=True)
    )

    flow_rows = _route_and_capacity_rows(queries, workspace["id"])
    assert flow_rows["routes"] == {"write_script": ("agent", "historical-write_script")}
    assert flow_rows["capacities"] == {}


def test_archived_agent_never_rewrites_or_prunes_frozen_routes(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws1")
    seed_workspace_agent_definitions(workspace["id"])
    service = WorkflowRevisionService(queries)
    definition = _agent_nodes_definition(review_as_local=False)
    service.publish_workspace_revision(workspace["id"], definition)
    with queries.connect() as conn:
        conn.execute(
            "insert into workspace_node_routes(workspace_id, node_key, target_kind, target_id)"
            " values (%s, 'write_script', 'agent', 'example-write-script-v1')",
            (workspace["id"],),
        )

    replace_agent_catalog(workspace["id"], {})
    service.publish_workspace_revision(workspace["id"], definition)

    rows = _route_and_capacity_rows(queries, workspace["id"])
    assert rows["routes"]["write_script"] == ("agent", "example-write-script-v1")


def test_publish_rejects_legacy_agent_node_whatever_the_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#935 gate flip: an agent-typed node without its own execution profile
    fails publish whatever the Agent catalog holds — here even an ambiguous
    one (two published Agents per capability, stubbed: the DB partial unique
    index makes it unrepresentable) is never consulted for resolution."""
    from server.app.agent_catalog import AgentDefinition

    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ws1")
    ambiguous = {
        "write-script-v1": AgentDefinition(
            capability="write_script", runtime="velites", skill="example/write-script"
        ),
        "write-script-v2": AgentDefinition(
            capability="write_script", runtime="velites", skill="example/write-script-v2"
        ),
    }
    monkeypatch.setattr(
        "server.app.services.workflow_draft_publish_gates.legacy_agent_catalog",
        lambda _dsn, _workspace_id: ambiguous,
    )

    errors = validate_workflow_for_publish(
        definition=_agent_nodes_definition(review_as_local=True),
        workspace_id=workspace["id"],
        job_db=queries,
        custom_nodes_enabled=True,
    )

    assert any(
        "Agent node write_script must declare its own execution profile" in error
        for error in errors
    )
    assert not any("exactly one published Agent" in error for error in errors)
