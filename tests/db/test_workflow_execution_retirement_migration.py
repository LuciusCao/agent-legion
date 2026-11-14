"""Schema v105 (#805): retire stored workflow-level execution defaults."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest
import yaml

from server.app.db.migrations.workflow_execution_defaults_retirement import (
    migrate_workflow_execution_defaults_retirement,
)
from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from server.app.workflows.loader import workflow_definition_from_dict
from tests.postgres_support import TEST_DATABASE_URL

_DEFAULTS = {
    "runtime": "velites",
    "provider": "default-provider",
    "model": "default-model",
    "thinking": "high",
    "prompt": "",
    "prompt_mode": "",
}
_PINS = {"code": {"version": 7, "definition_hash": "code-hash"}}
_PROVENANCE = {"first": {"agent_id": "old-agent", "version": 3, "restore": {"runtime": ""}}}


def _active_payload(workspace_id: str) -> dict[str, Any]:
    return {
        "key": workspace_id,
        "label": "Mixed overrides",
        "schema_version": 2,
        "intake": {"modes": {}},
        "execution": copy.deepcopy(_DEFAULTS),
        "nodes": {
            "start": {
                "node_type": "start",
                "label": "Start",
                "accepted_item_types": ["material", "ref"],
            },
            "first": {
                "node_type": "agent",
                "label": "First",
                "capability": "first",
                "execution": {
                    "runtime": "",
                    "provider": "node-provider",
                    "model": "",
                    "thinking": "",
                    "prompt": "keep-first",
                    "prompt_mode": "overwrite",
                },
            },
            "second": {
                "node_type": "agent",
                "label": "Second",
                "capability": "second",
                "execution": {
                    "runtime": "pi",
                    "provider": "",
                    "model": "node-model",
                    "thinking": "low",
                    "prompt": "keep-second",
                    "prompt_mode": "append",
                },
            },
            "code": {
                "node_type": "code",
                "label": "Code",
                "capability": "code",
                "execution": {
                    "runtime": "",
                    "provider": "dead-code-provider",
                    "model": "dead-code-model",
                    "thinking": "",
                    "prompt": "",
                    "prompt_mode": "",
                },
                "config": {"keep": True},
            },
            "gate": {
                "node_type": "approval",
                "label": "Gate",
                "execution": {"provider": "dead-approval-provider"},
                "config": {"rework_target": "first"},
            },
        },
        "edges": [
            {"source": "start", "target": "first", "condition": None},
            {"source": "first", "target": "second", "condition": None},
            {"source": "second", "target": "code", "condition": None},
            {"source": "code", "target": "gate", "condition": None},
        ],
        "node_code_pins": copy.deepcopy(_PINS),
        "agent_profile_provenance": copy.deepcopy(_PROVENANCE),
    }


def _draft_payload(workspace_id: str) -> dict[str, Any]:
    payload = _active_payload(workspace_id)
    payload["nodes"] = {
        key: {("type" if field == "node_type" else field): value for field, value in node.items()}
        for key, node in payload["nodes"].items()
    }
    payload["edges"] = [{"from": edge["source"], "to": edge["target"]} for edge in payload["edges"]]
    payload.pop("node_code_pins")
    payload.pop("agent_profile_provenance")
    return payload


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _pure_hash(payload: dict[str, Any]) -> str:
    pure = {
        key: value
        for key, value in payload.items()
        if key not in ("node_code_pins", "agent_profile_provenance")
    }
    return hashlib.sha256(_canonical(pure).encode("utf-8")).hexdigest()


def _insert_revision(conn, workspace_id: str, status: str, version: int, text: str) -> None:
    conn.execute(
        "insert into workflow_revisions(id, workspace_id, version, status, definition_json,"
        " definition_hash, created_at, published_at) values"
        " (%s, %s, %s, %s, %s, %s, timestamptz '2020-01-01', timestamptz '2020-01-02')",
        (
            f"rev-{workspace_id}-{status}",
            workspace_id,
            version,
            status,
            text,
            f"old-hash-{workspace_id}-{status}",
        ),
    )


def _seed_workspace(conn, workspace_id: str, *, with_draft: bool = True) -> dict[str, str]:
    conn.execute("insert into workspaces(id, name) values (%s, %s)", (workspace_id, workspace_id))
    active = _canonical(_active_payload(workspace_id))
    archived = _canonical({**_active_payload(workspace_id), "label": "Archived"})
    _insert_revision(conn, workspace_id, "active", 1, active)
    _insert_revision(conn, workspace_id, "archived", 2, archived)
    draft = yaml.safe_dump(_draft_payload(workspace_id), allow_unicode=True, sort_keys=False)
    if with_draft:
        conn.execute(
            "insert into workspace_workflow_drafts(workspace_id, definition_yaml, created_at,"
            " updated_at) values (%s, %s, timestamptz '2020-01-01', timestamptz '2020-01-01')",
            (workspace_id, draft),
        )
        conn.execute(
            "insert into workspace_workflow_draft_generation(workspace_id, generation)"
            " values (%s, 7)",
            (workspace_id,),
        )
    return {"active": active, "archived": archived, "draft": draft}


def _row(conn, table: str, where: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = conn.execute(f"select * from {table} where {where}", params).fetchone()
    assert row is not None
    return dict(row)


def _backups(conn, workspace_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "select * from workflow_execution_retirement_backups where workspace_id=%s"
            " order by source",
            (workspace_id,),
        ).fetchall()
    ]


def test_mixed_active_and_draft_materialize_with_backup_hash_and_cas() -> None:
    workspace_id = "v105-mixed"
    with write_transaction(TEST_DATABASE_URL) as conn:
        original = _seed_workspace(conn, workspace_id)
        before_active = _row(conn, "workflow_revisions", "id=%s", (f"rev-{workspace_id}-active",))
        migrate_workflow_execution_defaults_retirement(conn)

    with read_connection(TEST_DATABASE_URL) as conn:
        active = _row(conn, "workflow_revisions", "id=%s", (f"rev-{workspace_id}-active",))
        archived = _row(conn, "workflow_revisions", "id=%s", (f"rev-{workspace_id}-archived",))
        draft_row = _row(conn, "workspace_workflow_drafts", "workspace_id=%s", (workspace_id,))
        generation = _row(
            conn, "workspace_workflow_draft_generation", "workspace_id=%s", (workspace_id,)
        )
        backups = _backups(conn, workspace_id)

    payload = json.loads(active["definition_json"])
    assert "execution" not in payload
    assert payload["nodes"]["first"]["execution"] == {
        "runtime": "velites",
        "provider": "node-provider",
        "model": "default-model",
        "thinking": "high",
        "prompt": "keep-first",
        "prompt_mode": "overwrite",
    }
    assert payload["nodes"]["second"]["execution"] == {
        "runtime": "pi",
        "provider": "default-provider",
        "model": "node-model",
        "thinking": "low",
        "prompt": "keep-second",
        "prompt_mode": "append",
    }
    for key in ("start", "code", "gate"):
        assert payload["nodes"][key] == _active_payload(workspace_id)["nodes"][key]
    assert payload["node_code_pins"] == _PINS
    assert payload["agent_profile_provenance"] == _PROVENANCE
    assert active["definition_hash"] == _pure_hash(payload)
    for field in ("id", "workspace_id", "version", "status", "created_at", "published_at"):
        assert active[field] == before_active[field]
    assert archived["definition_json"] == original["archived"]
    assert archived["definition_hash"] == f"old-hash-{workspace_id}-archived"

    before_definition = workflow_definition_from_dict(_active_payload(workspace_id))
    after_definition = workflow_definition_from_dict(payload)
    for key in ("first", "second"):
        assert before_definition.nodes[key].execution == after_definition.nodes[key].execution

    draft = yaml.safe_load(draft_row["definition_yaml"])
    assert "execution" not in draft
    assert draft["nodes"]["first"]["execution"]["provider"] == "node-provider"
    assert draft["nodes"]["first"]["execution"]["model"] == "default-model"
    assert draft["nodes"]["second"]["execution"]["runtime"] == "pi"
    assert draft["nodes"]["code"] == _draft_payload(workspace_id)["nodes"]["code"]
    assert generation["generation"] == 8
    assert draft_row["updated_at"] > before_active["created_at"]

    assert {backup["source"] for backup in backups} == {"active_revision", "draft"}
    active_backup = next(backup for backup in backups if backup["source"] == "active_revision")
    draft_backup = next(backup for backup in backups if backup["source"] == "draft")
    assert active_backup["original_text"] == original["active"]
    assert active_backup["original_hash"] == f"old-hash-{workspace_id}-active"
    assert draft_backup["original_text"] == original["draft"]
    report = json.loads(active_backup["report_json"])
    first = next(node for node in report["nodes"] if node["node_key"] == "first")
    second = next(node for node in report["nodes"] if node["node_key"] == "second")
    assert first["materialized_fields"] == ["runtime", "model", "thinking"]
    assert first["preserved_overrides"] == ["provider"]
    assert second["materialized_fields"] == ["provider"]
    assert second["preserved_overrides"] == ["runtime", "model", "thinking"]


def test_legacy_agent_without_runtime_is_not_invented() -> None:
    workspace_id = "v105-legacy"
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, workspace_id)
        for source in ("active", "draft"):
            payload = (
                _active_payload(workspace_id)
                if source == "active"
                else _draft_payload(workspace_id)
            )
            payload["execution"] = {"provider": "provider-only"}
            for node in payload["nodes"].values():
                node_type = node.get("node_type" if source == "active" else "type")
                if node_type == "agent":
                    node["execution"]["runtime"] = ""
            if source == "active":
                conn.execute(
                    "update workflow_revisions set definition_json=%s where id=%s",
                    (_canonical(payload), f"rev-{workspace_id}-active"),
                )
            else:
                conn.execute(
                    "update workspace_workflow_drafts set definition_yaml=%s where workspace_id=%s",
                    (yaml.safe_dump(payload, sort_keys=False), workspace_id),
                )
        migrate_workflow_execution_defaults_retirement(conn)
        active = _row(conn, "workflow_revisions", "id=%s", (f"rev-{workspace_id}-active",))
        backups = _backups(conn, workspace_id)
    nodes = json.loads(active["definition_json"])["nodes"]
    for key in ("first", "second"):
        assert "runtime" not in nodes[key]["execution"] or not nodes[key]["execution"]["runtime"]
    report = json.loads(next(b for b in backups if b["source"] == "active_revision")["report_json"])
    assert all(node["status"] == "legacy_without_runtime" for node in report["nodes"])


@pytest.mark.parametrize("empty_execution", [{}, None])
def test_empty_execution_is_removed(empty_execution: Any) -> None:
    workspace_id = f"v105-empty-{empty_execution is None}"
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, workspace_id)
        payload = _active_payload(workspace_id)
        payload["execution"] = empty_execution
        original_nodes = copy.deepcopy(payload["nodes"])
        conn.execute(
            "update workflow_revisions set definition_json=%s where id=%s",
            (_canonical(payload), f"rev-{workspace_id}-active"),
        )
        migrate_workflow_execution_defaults_retirement(conn)
        active = _row(conn, "workflow_revisions", "id=%s", (f"rev-{workspace_id}-active",))
        generation = _row(
            conn, "workspace_workflow_draft_generation", "workspace_id=%s", (workspace_id,)
        )
    migrated = json.loads(active["definition_json"])
    assert "execution" not in migrated
    assert migrated["nodes"] == original_nodes
    assert generation["generation"] == 8


def test_replay_is_byte_idempotent_and_does_not_duplicate_backups() -> None:
    workspace_id = "v105-idempotent"
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, workspace_id)
        migrate_workflow_execution_defaults_retirement(conn)
        first = (
            _row(conn, "workflow_revisions", "id=%s", (f"rev-{workspace_id}-active",)),
            _row(conn, "workspace_workflow_drafts", "workspace_id=%s", (workspace_id,)),
            _row(conn, "workspace_workflow_draft_generation", "workspace_id=%s", (workspace_id,)),
            _backups(conn, workspace_id),
        )
        migrate_workflow_execution_defaults_retirement(conn)
        second = (
            _row(conn, "workflow_revisions", "id=%s", (f"rev-{workspace_id}-active",)),
            _row(conn, "workspace_workflow_drafts", "workspace_id=%s", (workspace_id,)),
            _row(conn, "workspace_workflow_draft_generation", "workspace_id=%s", (workspace_id,)),
            _backups(conn, workspace_id),
        )
    assert first == second


@pytest.mark.parametrize(
    ("bad_yaml", "reason"),
    [
        ("nodes: [unterminated", "invalid_yaml"),
        (_canonical({"execution": {"unknown": "drop-me"}, "nodes": {}}), "unknown"),
        (_canonical({"execution": {"model": 3}, "nodes": {}}), "must be a string"),
        (_canonical({"execution": {"prompt": "not-allowed"}, "nodes": {}}), "node-level only"),
        (
            _canonical(
                {
                    "key": "broken",
                    "label": "Broken",
                    "execution": {"provider": "default"},
                    "nodes": {"agent": {"type": "agent", "capability": "a", "execution": []}},
                }
            ),
            "must be a mapping",
        ),
    ],
)
def test_unresolved_draft_is_backed_up_unchanged_without_blocking_active(
    bad_yaml: str, reason: str
) -> None:
    workspace_id = "v105-unresolved"
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, workspace_id)
        conn.execute(
            "update workspace_workflow_drafts set definition_yaml=%s where workspace_id=%s",
            (bad_yaml, workspace_id),
        )
        migrate_workflow_execution_defaults_retirement(conn)
        active = _row(conn, "workflow_revisions", "id=%s", (f"rev-{workspace_id}-active",))
        draft = _row(conn, "workspace_workflow_drafts", "workspace_id=%s", (workspace_id,))
        generation = _row(
            conn, "workspace_workflow_draft_generation", "workspace_id=%s", (workspace_id,)
        )
        backups = _backups(conn, workspace_id)
        migrate_workflow_execution_defaults_retirement(conn)
        assert len(_backups(conn, workspace_id)) == 2
    assert "execution" not in json.loads(active["definition_json"])
    assert draft["definition_yaml"] == bad_yaml
    assert generation["generation"] == 7
    draft_backup = next(backup for backup in backups if backup["source"] == "draft")
    assert draft_backup["original_text"] == bad_yaml
    report = json.loads(draft_backup["report_json"])
    assert report["status"] == "unresolved"
    assert reason in report["reason"]


def test_active_failure_rolls_back_all_sources_and_backups() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "a-good")
        _seed_workspace(conn, "z-bad")
        bad = _active_payload("z-bad")
        bad["execution"] = {"unknown": "must-not-disappear"}
        conn.execute(
            "update workflow_revisions set definition_json=%s where id='rev-z-bad-active'",
            (_canonical(bad),),
        )
    with read_connection(TEST_DATABASE_URL) as conn:
        before = {
            table: [
                dict(row) for row in conn.execute(f"select * from {table} order by 1").fetchall()
            ]
            for table in (
                "workflow_revisions",
                "workspace_workflow_drafts",
                "workspace_workflow_draft_generation",
            )
        }
    with (
        pytest.raises(ValueError, match="unknown workflow execution keys"),
        write_transaction(TEST_DATABASE_URL) as conn,
    ):
        migrate_workflow_execution_defaults_retirement(conn)
    with read_connection(TEST_DATABASE_URL) as conn:
        after = {
            table: [
                dict(row) for row in conn.execute(f"select * from {table} order by 1").fetchall()
            ]
            for table in before
        }
        assert after == before
        assert (
            conn.execute(
                "select count(*) as n from workflow_execution_retirement_backups"
            ).fetchone()["n"]
            == 0
        )


def test_active_error_is_detected_before_any_source_write() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "a-good")
        _seed_workspace(conn, "z-bad")
        bad = _active_payload("z-bad")
        bad["execution"] = {"unknown": "must-not-disappear"}
        conn.execute(
            "update workflow_revisions set definition_json=%s where id='rev-z-bad-active'",
            (_canonical(bad),),
        )
        before = {
            table: [
                dict(row) for row in conn.execute(f"select * from {table} order by 1").fetchall()
            ]
            for table in (
                "workflow_revisions",
                "workspace_workflow_drafts",
                "workspace_workflow_draft_generation",
            )
        }
        with pytest.raises(ValueError, match="unknown workflow execution keys"):
            migrate_workflow_execution_defaults_retirement(conn)
        after = {
            table: [
                dict(row) for row in conn.execute(f"select * from {table} order by 1").fetchall()
            ]
            for table in before
        }
        assert after == before
        assert (
            conn.execute(
                "select count(*) as n from workflow_execution_retirement_backups"
            ).fetchone()["n"]
            == 0
        )


def test_draft_cas_conflict_rolls_back_instead_of_overwriting() -> None:
    class RacingConnection:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, params=()):
            if sql.startswith("update workspace_workflow_drafts set definition_yaml"):
                self.conn.execute(
                    "update workspace_workflow_drafts set definition_yaml=definition_yaml || %s"
                    " where workspace_id=%s",
                    ("\n# concurrent edit", "v105-cas"),
                )
            return self.conn.execute(sql, params)

    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "v105-cas")
    with read_connection(TEST_DATABASE_URL) as conn:
        before = _row(conn, "workspace_workflow_drafts", "workspace_id='v105-cas'")
    with (
        pytest.raises(ValueError, match="draft changed during v105"),
        write_transaction(TEST_DATABASE_URL) as conn,
    ):
        migrate_workflow_execution_defaults_retirement(RacingConnection(conn))
    with read_connection(TEST_DATABASE_URL) as conn:
        assert _row(conn, "workspace_workflow_drafts", "workspace_id='v105-cas'") == before
        assert (
            _row(conn, "workspace_workflow_draft_generation", "workspace_id='v105-cas'")[
                "generation"
            ]
            == 7
        )
        assert _backups(conn, "v105-cas") == []


def test_jobs_manifest_node_runs_and_runtime_history_stay_byte_identical() -> None:
    workspace_id = "v105-frozen"
    with write_transaction(TEST_DATABASE_URL) as conn:
        seeded = _seed_workspace(conn, workspace_id, with_draft=False)
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, workflow_revision_id,"
            " workflow_definition_hash, workflow_definition_snapshot_json) values"
            " ('job-v105', %s, 'question', 'item-1', %s, 'frozen-hash', %s)",
            (workspace_id, f"rev-{workspace_id}-active", seeded["active"]),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values ('job-v105', 'first')")
        conn.execute(
            "insert into node_runs(job_id, node_key, status, command_json) values"
            " ('job-v105', 'first', 'completed', '[\"keep\"]')"
        )
        manifest = '{"execution_defaults":{"runtime":"velites"},"keep":[1,2,3]}'
        conn.execute(
            "insert into agent_execution_requests(execution_id, workspace_id, job_id, node_key,"
            " agent_id, agent_definition_hash, node_concurrency_limit, state, queued_at,"
            " manifest_json) values ('exec-v105', %s, 'job-v105', 'first', 'agent-v1',"
            " 'agent-hash', 1, 'queued', current_timestamp, %s)",
            (workspace_id, manifest),
        )
        history = '{"execution":{"provider":"old"},"nodes":{"first":{"model":"old-model"}}}'
        conn.execute(
            "insert into workflow_runtime_config_versions(id, workspace_id, revision_id, version,"
            " config_json, changed_keys_json, created_by) values"
            " ('history-v105', %s, %s, 1, %s, '[\"execution.provider\"]', 'tester')",
            (workspace_id, f"rev-{workspace_id}-active", history),
        )
        before = {
            "jobs": _row(conn, "jobs", "id='job-v105'"),
            "agent_execution_requests": _row(
                conn, "agent_execution_requests", "execution_id='exec-v105'"
            ),
            "node_runs": _row(conn, "node_runs", "job_id='job-v105'"),
            "workflow_runtime_config_versions": _row(
                conn, "workflow_runtime_config_versions", "id='history-v105'"
            ),
        }
        migrate_workflow_execution_defaults_retirement(conn)
        after = {
            "jobs": _row(conn, "jobs", "id='job-v105'"),
            "agent_execution_requests": _row(
                conn, "agent_execution_requests", "execution_id='exec-v105'"
            ),
            "node_runs": _row(conn, "node_runs", "job_id='job-v105'"),
            "workflow_runtime_config_versions": _row(
                conn, "workflow_runtime_config_versions", "id='history-v105'"
            ),
        }
    assert after == before


def test_fresh_schema_registers_v105_and_backup_table() -> None:
    assert SCHEMA_VERSION == 105
    with read_connection(TEST_DATABASE_URL) as conn:
        migration = conn.execute("select name from schema_migrations where version=105").fetchone()
        table = conn.execute(
            "select to_regclass('workflow_execution_retirement_backups') as name"
        ).fetchone()
    assert migration is not None
    assert migration["name"] == "workflow_execution_defaults_retirement"
    assert table["name"] is not None


@pytest.mark.fresh_schema
def test_upgrade_from_v104_runs_materialization_once() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "v105-upgrade")
        conn.execute("delete from schema_migrations where version > 104")
        conn.execute("drop table workflow_execution_retirement_backups")
    init_db(TEST_DATABASE_URL)
    init_db(TEST_DATABASE_URL)
    with read_connection(TEST_DATABASE_URL) as conn:
        active = _row(conn, "workflow_revisions", "id='rev-v105-upgrade-active'")
        generation = _row(
            conn, "workspace_workflow_draft_generation", "workspace_id='v105-upgrade'"
        )
        backups = _backups(conn, "v105-upgrade")
        migration = conn.execute("select name from schema_migrations where version=105").fetchone()
    assert "execution" not in json.loads(active["definition_json"])
    assert generation["generation"] == 8
    assert len(backups) == 2
    assert migration is not None
