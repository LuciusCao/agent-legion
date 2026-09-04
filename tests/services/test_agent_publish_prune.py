"""Agent-publish override prune tests (#430).

AgentService.publish must prune workspace overrides of every workflow whose
ACTIVE revision routes an ``agent`` node to the just-published capability —
the same pure pruner the revision-side publish uses (#428). Without it, a
published Agent that renames/deletes a property or drops its whole
config_schema leaves stale override keys behind, and intake (which
re-validates by the LIVE agent schema) fails every new job of the workspace.

The scenarios pin the issue's own reproduction first (Agent drops its whole
schema → stale key → intake raises), then the happy repairs, the secret
marker semantics carried over from #428 (P1-1/P1-3), and the scope guards
(unrelated workspaces / capabilities untouched, publish stays green when
the prune itself fails).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.jobs.queries import JobQueries
from server.app.services.agent_service import AgentService
from server.app.services.node_config import resolve_workflow_node_configs
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import workflow_definition_from_mapping
from tests.postgres_support import TEST_DATABASE_URL

_WORKFLOW_KEY = "agent_nodes_flow"


def _publish_revision(queries: JobQueries, workspace_id: str) -> None:
    """Publish the two-agent-node workflow as the workspace's active revision."""
    definition = workflow_definition_from_mapping(
        {
            "key": _WORKFLOW_KEY,
            "label": "Agent Nodes Flow",
            "nodes": {
                "write_script": {"type": "agent", "capability": "write_script"},
                "review_script": {
                    "type": "agent",
                    "capability": "review_script",
                    "after": ["write_script"],
                },
            },
        }
    )
    WorkflowRevisionService(queries).publish_workspace_revision(workspace_id, definition)


def _agent_with_schema(properties: dict) -> AgentDefinition:
    return AgentDefinition(
        capability="review_script",
        runtime="velites",
        config_schema={"type": "object", "properties": properties},
    )


def _service(queries: JobQueries, workspace_id: str) -> AgentService:
    return AgentService(queries, workspace_id)


def _stored_overrides(queries: JobQueries, workspace_id: str) -> dict:
    return queries.get_workspace(workspace_id)["node_config"].get(_WORKFLOW_KEY, {})


def _publish_agent(queries: JobQueries, workspace_id: str, agent: AgentDefinition) -> None:
    """Save a draft and publish it through the service (the #430 path)."""
    service = _service(queries, workspace_id)
    service.save_draft("review-script-v1", agent, "user:test")
    service.publish("review-script-v1")


def test_agent_publish_prunes_renamed_override_key(tmp_path: Path) -> None:
    """#430 主场景: the Agent's schema renames a property → the stale override
    key is pruned, sibling keys survive, and intake (which re-validates by
    the live agent schema) resolves cleanly where it raised before."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ag-prune-ws", default_workflow_key=_WORKFLOW_KEY)
    _publish_revision(queries, workspace["id"])

    # v1 of the Agent declares old_key; the workspace overrides it.
    _publish_agent(queries, workspace["id"], _agent_with_schema({"old_key": {"type": "integer"}}))
    queries.update_workspace(
        workspace["id"],
        node_config={_WORKFLOW_KEY: {"review_script": {"old_key": 5, "kept": "v"}}},
    )

    # Before the fix: the rename leaves old_key behind and intake raises
    # 'node declares config but its capability has no config_schema'-style
    # errors on every new job. Publish v2 with the rename:
    _publish_agent(
        queries,
        workspace["id"],
        _agent_with_schema(
            {"new_key": {"type": "integer", "default": 1}, "kept": {"type": "string"}}
        ),
    )

    stored = _stored_overrides(queries, workspace["id"])
    assert stored["review_script"] == {"kept": "v"}
    # Intake's frozen resolution succeeds with the new schema.
    published = _service(queries, workspace["id"]).get_published_definition("review-script-v1")
    resolved = resolve_workflow_node_configs(
        workflow_definition_from_mapping(
            {
                "key": _WORKFLOW_KEY,
                "label": "Agent Nodes Flow",
                "nodes": {
                    "write_script": {"type": "agent", "capability": "write_script"},
                    "review_script": {
                        "type": "agent",
                        "capability": "review_script",
                        "after": ["write_script"],
                    },
                },
            }
        ),
        {"review-script-v1": published},
        queries.get_workspace(workspace["id"]),
    )
    assert resolved["review_script"]["new_key"] == 1


def test_agent_publish_prunes_whole_override_when_schema_dropped(tmp_path: Path) -> None:
    """#430 复现路径: the published Agent drops its whole config_schema →
    the routed node's override is cleared entirely (the empty-schema +
    non-empty-override state is what resolve_node_config rejects at intake);
    overrides of nodes routed to OTHER capabilities stay untouched."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ag-prune-empty-ws", default_workflow_key=_WORKFLOW_KEY)
    _publish_revision(queries, workspace["id"])

    schema_agent = _agent_with_schema({"old_key": {"type": "integer"}})
    _publish_agent(queries, workspace["id"], schema_agent)
    # A second Agent (another capability) keeps its own override too.
    other_agent = AgentDefinition(
        capability="write_script",
        runtime="velites",
        config_schema={"type": "object", "properties": {"tone": {"type": "string"}}},
    )
    other_service = AgentService(queries, workspace["id"])
    other_service.save_draft("write-script-v1", other_agent, "user:test")
    other_service.publish("write-script-v1")
    queries.update_workspace(
        workspace["id"],
        node_config={
            _WORKFLOW_KEY: {
                "review_script": {"old_key": 5},
                "write_script": {"tone": "formal"},
            }
        },
    )

    # The reproduction from the issue: publish a schema-less Agent; before
    # the fix the stale {'old_key': 5} survived and intake raised.
    _publish_agent(
        queries, workspace["id"], AgentDefinition(capability="review_script", runtime="velites")
    )

    stored = _stored_overrides(queries, workspace["id"])
    assert "review_script" not in stored
    # The other capability's node was pruned by ITS schema only (nothing).
    assert stored["write_script"] == {"tone": "formal"}


def test_agent_publish_keeps_secret_ref_marker_and_prunes_flipped_one(tmp_path: Path) -> None:
    """#428 P1-1/P1-3 semantics carried to the Agent-publish path: a
    {"secret_ref": ...} marker survives while the field stays secret, and a
    marker under a field that flips back to plain is an ordinary value the
    generic validation rejects (dict-under-string) — pruned here instead of
    blocking intake. Plaintext under a NEWLY-secret field is deleted, never
    migrated into the vault."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ag-prune-secret-ws", default_workflow_key=_WORKFLOW_KEY)
    _publish_revision(queries, workspace["id"])
    marker = {"secret_ref": "node:agent_nodes_flow:review_script:token"}

    # v1: token secret with a stored marker, kept plain.
    _publish_agent(
        queries,
        workspace["id"],
        _agent_with_schema(
            {"token": {"type": "string", "secret": True}, "kept": {"type": "string"}}
        ),
    )
    queries.update_workspace(
        workspace["id"],
        node_config={_WORKFLOW_KEY: {"review_script": {"token": marker, "kept": "v"}}},
    )

    # v2 keeps the field secret: the marker (genuine vault wiring written by
    # the settings PATCH chain) survives the prune.
    _publish_agent(
        queries,
        workspace["id"],
        _agent_with_schema(
            {"token": {"type": "string", "secret": True}, "kept": {"type": "string"}}
        ),
    )
    assert _stored_overrides(queries, workspace["id"])["review_script"] == {
        "token": marker,
        "kept": "v",
    }

    # v3 flips the field back to plain: the marker is now an ordinary value,
    # the dict shape fails the string check, and the prune removes it.
    queries.update_workspace(
        workspace["id"],
        node_config={_WORKFLOW_KEY: {"review_script": {"token": marker, "kept": "v"}}},
    )
    _publish_agent(
        queries,
        workspace["id"],
        _agent_with_schema({"token": {"type": "string"}, "kept": {"type": "string"}}),
    )
    assert _stored_overrides(queries, workspace["id"])["review_script"] == {"kept": "v"}

    # v4 flips a PLAIN field (with plaintext stored) to secret: the plaintext
    # is deleted — pushing it into the vault would bless data that never
    # passed the vault's write path (P1-1's delete-not-migrate rule).
    queries.update_workspace(
        workspace["id"],
        node_config={_WORKFLOW_KEY: {"review_script": {"token": "plain-text", "kept": "v"}}},
    )
    _publish_agent(
        queries,
        workspace["id"],
        _agent_with_schema(
            {"token": {"type": "string", "secret": True}, "kept": {"type": "string"}}
        ),
    )
    assert _stored_overrides(queries, workspace["id"])["review_script"] == {"kept": "v"}


def test_agent_publish_prune_scopes_to_referencing_workspaces_only(tmp_path: Path) -> None:
    """Scope guard: a workspace whose active revision does NOT route the
    published capability keeps its overrides verbatim — including a node
    keyed the same, whose workflow-level judgment belongs to that workflow's
    own schemas (none here), not to the unrelated Agent publish."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")

    # Workspace A routes review_script agents; workspace B's workflow has no
    # agent node for the capability at all (only a code node).
    ws_a = queries.create_workspace("ag-prune-scope-a", default_workflow_key=_WORKFLOW_KEY)
    ws_b = queries.create_workspace("ag-prune-scope-b", default_workflow_key="other_flow")
    _publish_revision(queries, ws_a["id"])
    WorkflowRevisionService(queries).publish_workspace_revision(
        ws_b["id"],
        workflow_definition_from_mapping(
            {
                "key": "other_flow",
                "label": "Other Flow",
                "nodes": {
                    "review_script": {"type": "code", "capability": "unrelated_cap"},
                },
            }
        ),
    )

    _publish_agent(queries, ws_a["id"], _agent_with_schema({"old_key": {"type": "integer"}}))
    stale_a = {_WORKFLOW_KEY: {"review_script": {"old_key": 5}}}
    untouched_b = {"other_flow": {"review_script": {"old_key": 5}}}
    queries.update_workspace(ws_a["id"], node_config=stale_a)
    queries.update_workspace(ws_b["id"], node_config=untouched_b)

    # Rename the property: A is pruned, B keeps its (unrelated) overrides.
    _publish_agent(queries, ws_a["id"], _agent_with_schema({"new_key": {"type": "integer"}}))

    assert "review_script" not in _stored_overrides(queries, ws_a["id"])
    assert queries.get_workspace(ws_b["id"])["node_config"] == untouched_b


def test_agent_publish_stays_green_when_prune_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure semantics: the prune runs AFTER the publish commits and never
    fails the publish — a broken prune leaves the definition published and
    the overrides exactly as they were (intake keeps failing for that
    workspace until the next publish retries, which is the pre-fix state)."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ag-prune-fail-ws", default_workflow_key=_WORKFLOW_KEY)
    _publish_revision(queries, workspace["id"])
    _publish_agent(queries, workspace["id"], _agent_with_schema({"old_key": {"type": "integer"}}))
    stale = {_WORKFLOW_KEY: {"review_script": {"old_key": 5}}}
    queries.update_workspace(workspace["id"], node_config=stale)

    # Break the prune's first workspace read AFTER the publish committed:
    # the exception surfaces inside prune_agent_overrides' own guard.
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("prune exploded after publish")

    monkeypatch.setattr(queries, "get_workspace", _boom)

    # The publish itself must succeed despite the prune explosion.
    service = _service(queries, workspace["id"])
    service.save_draft(
        "review-script-v1", _agent_with_schema({"new_key": {"type": "integer"}}), "user:test"
    )
    entity = service.publish("review-script-v1")

    assert entity.status == "published"
    monkeypatch.undo()
    # The definition v2 is live; the overrides keep their stale values.
    assert service.get_published_definition("review-script-v1") is not None
    assert queries.get_workspace(workspace["id"])["node_config"] == stale


def test_agent_publish_without_overrides_is_a_noop_prune(tmp_path: Path) -> None:
    """No stored overrides → the publish writes no workspace row at all."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ag-prune-noop-ws", default_workflow_key=_WORKFLOW_KEY)
    _publish_revision(queries, workspace["id"])

    _publish_agent(queries, workspace["id"], _agent_with_schema({"old_key": {"type": "integer"}}))

    assert queries.get_workspace(workspace["id"])["node_config"] == {}


def test_agent_rollback_prunes_overrides_too(tmp_path: Path) -> None:
    """Rollback re-publishes an old definition — the same live-schema
    staleness applies, so the same post-commit prune runs: rolling back to
    the renamed version prunes the stale key the rollback target dropped."""
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace = queries.create_workspace("ag-prune-rollback-ws", default_workflow_key=_WORKFLOW_KEY)
    _publish_revision(queries, workspace["id"])

    service = _service(queries, workspace["id"])
    # v1 declares old_key; the workspace overrides it.
    v1 = _agent_with_schema({"old_key": {"type": "integer"}})
    service.save_draft("review-script-v1", v1, "user:test")
    service.publish("review-script-v1")
    queries.update_workspace(
        workspace["id"],
        node_config={_WORKFLOW_KEY: {"review_script": {"old_key": 5}}},
    )

    # v2 renames the property; publish v2 (its own prune keeps old_key gone,
    # then a raw update replants it — the stale-residue shape the fix
    # targets, e.g. left by a concurrent settings PATCH racing the prune).
    v2 = _agent_with_schema({"new_key": {"type": "integer", "default": 1}})
    service.save_draft("review-script-v1", v2, "user:test")
    service.publish("review-script-v1")
    queries.update_workspace(
        workspace["id"],
        node_config={_WORKFLOW_KEY: {"review_script": {"old_key": 5}}},
    )

    # Roll back to v2 — the old_key override must go with it.
    service.rollback("review-script-v1", 2, "user:test")

    assert _stored_overrides(queries, workspace["id"]) == {}
