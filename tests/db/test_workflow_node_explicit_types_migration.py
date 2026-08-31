"""Schema v66 data migration: explicit workflow node types (#284 phase 2).

Pre-v66 stored payloads carry the legacy ``node`` node_type (or none, in
draft YAML); v66 backfills ``agent``/``code`` from the materialized
``workspace_node_routes`` projection so the loader normalization produces
no ghost structural diff. These tests direct-call the migration against
seeded pre-v66 payloads, pin idempotent replay, hash recomputation (pins
stay out of the hash), the approval-node skip, and the loader-equivalence
rule.
"""

from __future__ import annotations

import hashlib
import json

import pytest
import yaml

from server.app.db.migrations.workflow_node_explicit_types import (
    migrate_workflow_node_explicit_types,
)
from server.app.db.schema import init_db
from server.app.db.transaction import read_connection, write_transaction
from server.app.workflows.definition import workflow_definition_from_dict
from tests.helpers.legacy_workflow_key_shape import (
    narrow_back_to_v70,
    restore_pre_v70_shape,
)
from tests.postgres_support import TEST_DATABASE_URL

_WORKSPACE = "v65-node-types-ws"


# #211 M2 (schema v70): the revision-backfill half of the v66 migration is
# shape-guarded and returns early on the current schema (workflow_revisions
# lost its workflow_key column, which the projection join keyed on). The
# revision tests below seeded pre-v66 payloads to exercise that half, so they
# cannot run against a fresh v70 database — the draft-YAML tests still cover
# the surviving backfill path (drafts never keyed on workflow_key).
@pytest.fixture
def legacy_shape_db() -> str:
    """v69 shape restored (workflow_key back on revisions/routes) so the v66
    revision backfill runs exactly as it does on real pre-v66→v70 upgrades
    (subagent review #334: shape-guarded no-ops still ship live on upgrades);
    torn back down to the terminal shape afterwards for later tests."""
    init_db(TEST_DATABASE_URL)
    with write_transaction(TEST_DATABASE_URL) as conn:
        restore_pre_v70_shape(conn)
    yield TEST_DATABASE_URL
    with write_transaction(TEST_DATABASE_URL) as conn:
        narrow_back_to_v70(conn)


# Pre-v65 asdict snapshot: non-start nodes carry the legacy "node" type.
_DEFINITION = {
    "edges": [
        {"condition": None, "source": "_start", "target": "gen"},
        {"condition": None, "source": "gen", "target": "pub"},
    ],
    "intake": {"modes": {}},
    "key": _WORKSPACE,
    "label": "Demo",
    "nodes": {
        "_start": {
            "accepted_item_types": ["material", "ref"],
            "after": [],
            "capability": "",
            "config": {},
            "config_schema": {},
            "execution": {"model": "", "provider": "", "prompt": "", "thinking": ""},
            "inputs": [],
            "label": "_start",
            "node_type": "start",
            "outputs": [],
            "reduce": None,
            "shard": None,
            "terminal": None,
        },
        "gen": {
            "accepted_item_types": ["material", "ref"],
            "after": [],
            "capability": "generate",
            "config": {},
            "config_schema": {},
            "execution": {"model": "", "provider": "", "prompt": "", "thinking": ""},
            "inputs": [],
            "label": "gen",
            "node_type": "node",
            "outputs": ["result.json"],
            "reduce": None,
            "shard": None,
            "terminal": None,
        },
        "pub": {
            "accepted_item_types": ["material", "ref"],
            "after": ["gen"],
            "capability": "publish",
            "config": {},
            "config_schema": {},
            "execution": {"model": "", "provider": "", "prompt": "", "thinking": ""},
            "inputs": ["result.json"],
            "label": "pub",
            "node_type": "node",
            "outputs": [],
            "reduce": None,
            "shard": None,
            "terminal": None,
        },
    },
    "schema_version": 2,
}

_DRAFT_YAML = f"""\
key: {_WORKSPACE}
label: Demo
nodes:
  _start:
    type: start
  gen:
    capability: generate
    after: [_start]
  pub:
    capability: publish
    after: [gen]
"""


def _seed_workspace(conn, workspace_id: str = _WORKSPACE) -> None:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key) values (%s, %s, %s)"
        " on conflict(id) do nothing",
        (workspace_id, workspace_id, workspace_id),
    )


def _seed_active_revision(conn, workspace_id: str, definition: dict) -> str:
    payload = {
        **definition,
        # Publish-time sibling: preserved in storage, excluded from the hash.
        "node_code_pins": {"pub": {"version": 3, "code_hash": "abc123"}},
    }
    definition_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    pure_json = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    revision_id = f"rev-{workspace_id}"
    conn.execute(
        "insert into workflow_revisions(id, workspace_id, version, status,"
        " definition_json, definition_hash)"
        " values (%s, %s, 1, 'active', %s, %s)",
        (
            revision_id,
            workspace_id,
            definition_json,
            hashlib.sha256(pure_json.encode("utf-8")).hexdigest(),
        ),
    )
    return revision_id


def _seed_agent_route(conn, workspace_id: str, node_key: str) -> None:
    conn.execute(
        "insert into workspace_node_routes(workspace_id, node_key, "
        " target_kind, target_id) values (%s, %s, 'agent', 'agent-1')",
        (workspace_id, node_key),
    )


def _seed_draft(conn, workspace_id: str, definition_yaml: str) -> None:
    conn.execute(
        "insert into workspace_workflow_drafts(workspace_id, definition_yaml) values (%s, %s)",
        (workspace_id, definition_yaml),
    )


def _revision_row(revision_id: str) -> dict:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select definition_json, definition_hash from workflow_revisions where id=%s",
            (revision_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def _draft_yaml(workspace_id: str) -> str:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select definition_yaml from workspace_workflow_drafts where workspace_id=%s",
            (workspace_id,),
        ).fetchone()
    assert row is not None
    return str(row["definition_yaml"])


def test_backfills_revision_node_types_from_route_projection(legacy_shape_db) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn)
        revision_id = _seed_active_revision(conn, _WORKSPACE, _DEFINITION)
        _seed_agent_route(conn, _WORKSPACE, "gen")
        migrate_workflow_node_explicit_types(conn)

    row = _revision_row(revision_id)
    payload = json.loads(row["definition_json"])
    assert payload["nodes"]["gen"]["node_type"] == "agent"
    assert payload["nodes"]["pub"]["node_type"] == "code"
    assert payload["nodes"]["_start"]["node_type"] == "start"
    # The pins sibling survives, and the hash covers the pure definition.
    assert payload["node_code_pins"] == {"pub": {"version": 3, "code_hash": "abc123"}}
    pure = {key: value for key, value in payload.items() if key != "node_code_pins"}
    pure_json = json.dumps(pure, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert row["definition_hash"] == hashlib.sha256(pure_json.encode("utf-8")).hexdigest()


def test_backfilled_revision_matches_loader_normalization(legacy_shape_db) -> None:
    """The backfill must equal what the loader produces from the pre-v66
    payload — otherwise the next save spawns a ghost structural revision."""
    pre_v66 = json.loads(json.dumps(_DEFINITION))
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn)
        revision_id = _seed_active_revision(conn, _WORKSPACE, _DEFINITION)
        _seed_agent_route(conn, _WORKSPACE, "gen")
        migrate_workflow_node_explicit_types(conn)

    payload = json.loads(_revision_row(revision_id)["definition_json"])
    migrated = workflow_definition_from_dict(payload)
    # Loader view of the pre-v66 payload: "node" normalizes to "code".
    assert {key: node.node_type for key, node in migrated.nodes.items()} == {
        "_start": "start",
        "gen": "agent",
        "pub": "code",
    }
    legacy = workflow_definition_from_dict(pre_v66)
    assert legacy.nodes["gen"].node_type == "code"


def test_approval_nodes_keep_their_explicit_type(legacy_shape_db) -> None:
    """Approval gates (EXEC-APPROVAL-001) are already explicit: the backfill
    must not rewrite them to code."""
    definition = json.loads(json.dumps(_DEFINITION))
    definition["nodes"]["pub"]["node_type"] = "approval"
    definition["nodes"]["pub"]["capability"] = ""
    draft_yaml = _DRAFT_YAML.replace("capability: publish", "type: approval")
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn)
        revision_id = _seed_active_revision(conn, _WORKSPACE, definition)
        _seed_agent_route(conn, _WORKSPACE, "gen")
        _seed_draft(conn, _WORKSPACE, draft_yaml)
        migrate_workflow_node_explicit_types(conn)

    payload = json.loads(_revision_row(revision_id)["definition_json"])
    assert payload["nodes"]["pub"]["node_type"] == "approval"
    assert payload["nodes"]["gen"]["node_type"] == "agent"
    draft_payload = yaml.safe_load(_draft_yaml(_WORKSPACE))
    assert draft_payload["nodes"]["pub"]["type"] == "approval"
    assert draft_payload["nodes"]["gen"]["type"] == "agent"


def test_replay_is_idempotent(legacy_shape_db) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn)
        revision_id = _seed_active_revision(conn, _WORKSPACE, _DEFINITION)
        _seed_agent_route(conn, _WORKSPACE, "gen")
        migrate_workflow_node_explicit_types(conn)
        migrate_workflow_node_explicit_types(conn)

    payload = json.loads(_revision_row(revision_id)["definition_json"])
    assert payload["nodes"]["gen"]["node_type"] == "agent"
    assert payload["nodes"]["pub"]["node_type"] == "code"


def test_backfills_draft_yaml_from_route_projection() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn)
        _seed_agent_route(conn, _WORKSPACE, "gen")
        _seed_draft(conn, _WORKSPACE, _DRAFT_YAML)
        migrate_workflow_node_explicit_types(conn)

    payload = yaml.safe_load(_draft_yaml(_WORKSPACE))
    assert payload["nodes"]["gen"]["type"] == "agent"
    assert payload["nodes"]["pub"]["type"] == "code"
    assert payload["nodes"]["_start"]["type"] == "start"


def test_fully_typed_draft_keeps_yaml_byte_identical() -> None:
    typed_yaml = _DRAFT_YAML.replace(
        "capability: generate", "type: agent\n    capability: generate"
    )
    typed_yaml = typed_yaml.replace("capability: publish", "type: code\n    capability: publish")
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn)
        _seed_agent_route(conn, _WORKSPACE, "gen")
        _seed_draft(conn, _WORKSPACE, typed_yaml)
        migrate_workflow_node_explicit_types(conn)

    assert _draft_yaml(_WORKSPACE) == typed_yaml


def test_draft_replay_is_idempotent() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn)
        _seed_agent_route(conn, _WORKSPACE, "gen")
        _seed_draft(conn, _WORKSPACE, _DRAFT_YAML)
        migrate_workflow_node_explicit_types(conn)
    first = _draft_yaml(_WORKSPACE)
    with write_transaction(TEST_DATABASE_URL) as conn:
        migrate_workflow_node_explicit_types(conn)

    assert _draft_yaml(_WORKSPACE) == first
