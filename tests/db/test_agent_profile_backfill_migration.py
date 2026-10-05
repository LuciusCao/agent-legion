"""Schema v93 (#935, #440 P3): inline Agent definitions into agent nodes.

Pins the data migration on a seeded workspace: active revision and Studio
draft backfill (route target vs capability resolution, shared definitions
expanded 1:N, config_schema overwrite, tools fill, skill sink), unresolved
nodes left untouched and reported, the backup table, the recomputed pure
``definition_hash`` with the provenance sibling outside it, the loader
round trip, and replay idempotence (including through ``init_db``).
"""

from __future__ import annotations

import hashlib
import json

import pytest
import yaml

from server.app.agent_catalog import AgentDefinition
from server.app.db.migrations.agent_profile_backfill import migrate_agent_profile_backfill
from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from server.app.workflows.definition import workflow_definition_from_dict
from server.app.workflows.loader import workflow_definition_from_mapping
from server.app.workflows.revision_format import serialize_definition
from tests.postgres_support import TEST_DATABASE_URL

_WS = "v93_ws"
_SKILL = "demo/review"

_GEN = AgentDefinition(
    capability="generate",
    runtime="velites",
    skill="demo/generate",
    tools=("read", "write"),
    requires_labels={"gpu": "yes"},
    config_schema={"type": "object", "properties": {"n": {"type": "integer", "default": 1}}},
)
_REVIEW = AgentDefinition(capability="review", runtime="pi", tools=("read",))
_OLD = AgentDefinition(capability="old", runtime="pi")
_FLAT_SKILL = AgentDefinition(capability="flat", runtime="pi", skill="flatskill")

_WORKFLOW_YAML = f"""
key: {_WS}
label: V93
nodes:
  gen:
    type: agent
    capability: generate
    outputs: [gen.json]
    config_schema:
      type: object
      properties:
        ignored: {{type: string}}
  review_a:
    type: agent
    capability: review
    after: [gen]
    tools: [bash]
    skill: {{key: {_SKILL}}}
  review_b:
    type: agent
    capability: review
    after: [gen]
    skill: {{key: {_SKILL}}}
  ghost:
    type: agent
    capability: ghost
    skill: {{key: {_SKILL}}}
  arch:
    type: agent
    capability: old
    skill: {{key: {_SKILL}}}
  flat:
    type: agent
    capability: flat
  done:
    type: agent
    capability: review
    skill: {{key: {_SKILL}}}
    execution: {{runtime: velites}}
  code1:
    type: code
    capability: code1
"""


def _insert_agent(conn, agent_id: str, definition: AgentDefinition, version: int, status: str):
    conn.execute(
        "insert into versioned_entities(id, entity_type, workspace_id, entity_key, version,"
        " status, definition_json, definition_hash, created_by, published_at)"
        " values (%s, 'agent', %s, %s, %s, %s, %s, %s, 'test', current_timestamp)",
        (
            f"agent:{_WS}:{agent_id}:v{version}",
            _WS,
            agent_id,
            version,
            status,
            json.dumps(definition.model_dump(mode="json"), sort_keys=True),
            definition.definition_hash(),
        ),
    )


def _seed(conn) -> str:
    conn.execute("insert into workspaces(id, name) values (%s, 'V93')", (_WS,))
    _insert_agent(conn, "gen_agent", _GEN, 1, "published")
    _insert_agent(conn, "review_agent", _REVIEW, 1, "published")
    _insert_agent(conn, "old_agent", _OLD, 1, "archived")
    _insert_agent(conn, "flat_agent", _FLAT_SKILL, 1, "published")
    definition = workflow_definition_from_mapping(yaml.safe_load(_WORKFLOW_YAML))
    stored = json.loads(serialize_definition(definition))
    stored["node_code_pins"] = {"code1": {"version": 3}}
    text = json.dumps(stored, sort_keys=True, separators=(",", ":"))
    conn.execute(
        "insert into workflow_revisions(id, workspace_id, version, status, definition_json,"
        " definition_hash, published_at) values (%s, %s, 1, 'active', %s, 'old', current_timestamp)",
        (f"{_WS}:v1", _WS, text),
    )
    # gen is routed (dispatch reads the route); arch routes to an archived Agent.
    for node_key, target in (("gen", "gen_agent"), ("arch", "old_agent")):
        conn.execute(
            "insert into workspace_node_routes(workspace_id, node_key, target_kind, target_id)"
            " values (%s, %s, 'agent', %s)",
            (_WS, node_key, target),
        )
    conn.execute(
        "insert into workspace_workflow_drafts(workspace_id, definition_yaml) values (%s, %s)",
        (_WS, _WORKFLOW_YAML),
    )
    return text


def _revision(conn) -> dict:
    return dict(
        conn.execute(
            "select definition_json, definition_hash from workflow_revisions where id=%s",
            (f"{_WS}:v1",),
        ).fetchone()
    )


def _draft(conn) -> str:
    return str(
        conn.execute(
            "select definition_yaml from workspace_workflow_drafts where workspace_id=%s", (_WS,)
        ).fetchone()["definition_yaml"]
    )


def _backups(conn) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "select source, source_id, original_text, original_hash, report_json"
            " from agent_profile_backfill_backups where workspace_id=%s order by source",
            (_WS,),
        ).fetchall()
    ]


def test_fresh_schema_records_v93_and_creates_the_backup_table() -> None:
    assert SCHEMA_VERSION >= 93
    with read_connection(TEST_DATABASE_URL) as conn:
        migration = conn.execute("select name from schema_migrations where version=93").fetchone()
        table = conn.execute("select to_regclass('agent_profile_backfill_backups') as t").fetchone()
    assert migration is not None and migration["name"] == "agent_profile_backfill"
    assert table["t"] is not None


def test_active_revision_nodes_are_inlined_from_the_agent_they_run() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        original = _seed(conn)
        migrate_agent_profile_backfill(conn)
    with read_connection(TEST_DATABASE_URL) as conn:
        revision = _revision(conn)
        backups = _backups(conn)
    payload = json.loads(revision["definition_json"])
    nodes = payload["nodes"]

    gen = nodes["gen"]
    assert gen["execution"]["runtime"] == "velites"
    assert gen["requires_labels"] == {"gpu": "yes"}
    assert gen["tools"] == ["read", "write"]
    # config_schema is overwritten (the node's own declaration was ignored).
    assert gen["config_schema"] == _GEN.config_schema
    # skill sinks from the definition (node bound none).
    assert gen["skill"] == {"key": "demo/generate", "ref": "latest"}

    # Shared definition expands 1:N; a node's own tools win.
    assert nodes["review_a"]["execution"]["runtime"] == "pi"
    assert nodes["review_a"]["tools"] == ["bash"]
    assert nodes["review_b"]["tools"] == ["read"]
    assert nodes["review_b"]["skill"] == {"key": _SKILL, "ref": "latest"}

    # Unresolved nodes stay untouched.
    for key in ("ghost", "arch", "flat"):
        assert nodes[key]["execution"].get("runtime", "") == ""
    assert nodes["code1"]["node_type"] == "code"

    provenance = payload["agent_profile_provenance"]
    assert set(provenance) == {"gen", "review_a", "review_b"}
    assert provenance["gen"]["agent_id"] == "gen_agent"
    assert provenance["gen"]["version"] == 1
    assert provenance["gen"]["definition_hash"] == _GEN.definition_hash()
    assert provenance["gen"]["restore"] == {
        "runtime": "",
        "requires_labels": {},
        "tools": [],
        "config_schema": {"type": "object", "properties": {"ignored": {"type": "string"}}},
        "skill": None,
    }
    assert provenance["review_a"]["restore"]["tools"] == ["bash"]
    assert payload["node_code_pins"] == {"code1": {"version": 3}}

    # Hash covers the pure definition (pins + provenance excluded) and
    # equals what re-publishing the loaded definition would compute.
    loaded = workflow_definition_from_dict(payload)
    assert (
        revision["definition_hash"]
        == hashlib.sha256(serialize_definition(loaded).encode("utf-8")).hexdigest()
    )
    assert loaded.nodes["gen"].execution.runtime == "velites"

    revision_backup = next(b for b in backups if b["source"] == "active_revision")
    assert revision_backup["original_text"] == original
    assert revision_backup["original_hash"] == "old"
    report = {n["node_key"]: n for n in json.loads(revision_backup["report_json"])["nodes"]}
    assert report["gen"]["status"] == "backfilled"
    assert report["gen"]["config_schema_overridden"] is True
    assert report["ghost"]["reason"] == "no_agent"
    assert report["arch"] == {
        "node_key": "arch",
        "capability": "old",
        "status": "unresolved",
        "reason": "archived",
        "agent_ids": ["old_agent"],
    }
    assert report["flat"]["reason"] == "skill_unportable"
    assert "done" not in report  # already self-contained
    assert "code1" not in report


def test_draft_is_rewritten_sparsely_and_loads() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed(conn)
        migrate_agent_profile_backfill(conn)
    with read_connection(TEST_DATABASE_URL) as conn:
        draft = yaml.safe_load(_draft(conn))
        backups = _backups(conn)
    nodes = draft["nodes"]
    assert nodes["gen"]["execution"] == {"runtime": "velites"}
    assert nodes["gen"]["requires_labels"] == {"gpu": "yes"}
    assert nodes["review_b"]["execution"] == {"runtime": "pi"}
    # Empty profile values stay out of the YAML (sparse echo).
    assert "requires_labels" not in nodes["review_b"]
    assert "config_schema" not in nodes["review_b"]
    assert "execution" not in nodes["ghost"]
    definition = workflow_definition_from_mapping(draft)
    assert definition.nodes["gen"].config_schema == _GEN.config_schema
    assert {b["source"] for b in backups} == {"active_revision", "draft"}


def test_replay_is_a_no_op() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed(conn)
        migrate_agent_profile_backfill(conn)
    with read_connection(TEST_DATABASE_URL) as conn:
        first = (_revision(conn), _draft(conn), len(_backups(conn)))
    with write_transaction(TEST_DATABASE_URL) as conn:
        migrate_agent_profile_backfill(conn)
    with read_connection(TEST_DATABASE_URL) as conn:
        second = (_revision(conn), _draft(conn), len(_backups(conn)))
    assert first == second


def test_workspace_without_legacy_agent_nodes_is_untouched() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("insert into workspaces(id, name) values ('v93_plain', 'P')")
        definition = workflow_definition_from_mapping(
            {
                "key": "v93_plain",
                "label": "P",
                "nodes": {"c": {"type": "code", "capability": "c"}},
            }
        )
        text = serialize_definition(definition)
        conn.execute(
            "insert into workflow_revisions(id, workspace_id, version, status, definition_json,"
            " definition_hash) values ('v93_plain:v1', 'v93_plain', 1, 'active', %s, 'h')",
            (text,),
        )
        migrate_agent_profile_backfill(conn)
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select definition_json, definition_hash from workflow_revisions"
            " where id='v93_plain:v1'"
        ).fetchone()
        backups = conn.execute(
            "select count(*) as n from agent_profile_backfill_backups where workspace_id='v93_plain'"
        ).fetchone()["n"]
    assert (row["definition_json"], row["definition_hash"]) == (text, "h")
    assert backups == 0


@pytest.mark.fresh_schema
def test_upgrade_from_v92_runs_the_backfill_once() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed(conn)
        conn.execute("delete from schema_migrations where version > 92")
        conn.execute("drop table agent_profile_backfill_backups")

    init_db(TEST_DATABASE_URL)
    init_db(TEST_DATABASE_URL)  # recorded at v93: no-op

    with read_connection(TEST_DATABASE_URL) as conn:
        payload = json.loads(_revision(conn)["definition_json"])
        migration = conn.execute("select name from schema_migrations where version=93").fetchone()
        backups = _backups(conn)
    assert migration is not None and migration["name"] == "agent_profile_backfill"
    assert payload["nodes"]["gen"]["execution"]["runtime"] == "velites"
    assert len(backups) == 2
