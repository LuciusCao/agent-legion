"""Schema v53: job_batches → runs (route A rename, materials-and-runs §8).

The upgrade keeps every historical row: batch rows move into ``runs`` with
their ids, the payload's pin keys land in ``runs.frozen_pins_json``, and the
frozen ``node_config`` / ``task_candidates`` sink onto the batch's jobs
(``jobs.frozen_config_json`` / ``jobs.input_json``, RUN-FREEZE-001).
"""

from __future__ import annotations

import json

import pytest

from server.app.db.migrations.runs import migrate_runs
from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _columns(conn, table: str) -> set[str]:
    return {
        row["column_name"]
        for row in conn.execute(
            "select column_name from information_schema.columns"
            " where table_schema=current_schema() and table_name=%s",
            (table,),
        ).fetchall()
    }


def _json(raw) -> dict:
    return json.loads(str(raw))


# The latest-migration record pin (SCHEMA_VERSION + recorded name) moved to
# tests/db/test_material_bundles_schema.py (v55).


def test_runs_baseline_shape() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with read_connection(TEST_DATABASE_URL) as conn:
        run_columns = _columns(conn, "runs")
        job_columns = _columns(conn, "jobs")
        job_batches = conn.execute("select to_regclass('job_batches')").fetchone()
    assert {
        "id",
        "workspace_id",
        "workflow_key",
        "source_kind",
        "status",
        "frozen_pins_json",
        "stats_json",
        "queue_payload_json",
        "created_count",
        "error_message",
        "created_by",
        "created_at",
        "updated_at",
    } <= run_columns
    assert "run_id" in job_columns
    assert "batch_id" not in job_columns
    assert {"input_json", "frozen_config_json"} <= job_columns
    assert job_batches["to_regclass"] is None


def _rebuild_v52_shape(conn) -> None:
    """Undo v53 so init_db replays the upgrade: v52 jobs + job_batches."""
    conn.execute("alter table jobs drop column if exists input_json")
    conn.execute("alter table jobs drop column if exists frozen_config_json")
    conn.execute("alter table jobs rename column run_id to batch_id")
    conn.execute("drop table if exists runs")
    conn.execute(
        """
        create table job_batches (
          id text primary key,
          workspace_id text not null references workspaces(id) on delete cascade,
          workflow_key text not null,
          source_kind text not null,
          source_payload_json text not null default '{}',
          status text not null default 'created',
          created_count integer not null default 0,
          error_message text not null default '',
          created_at timestamptz not null default current_timestamp,
          updated_at timestamptz not null default current_timestamp
        )
        """
    )
    conn.execute("delete from schema_migrations where version=%s", (SCHEMA_VERSION,))


def _seed_batch(conn, batch_id: str, payload: dict | str, status: str = "completed") -> None:
    conn.execute(
        """
        insert into job_batches(
          id, workspace_id, workflow_key, source_kind, source_payload_json,
          status, created_count, error_message
        ) values (%s, 'ws-run', 'wf_demo', 'direct_ids', %s, %s, 0, '')
        """,
        (batch_id, payload if isinstance(payload, str) else json.dumps(payload), status),
    )


def _seed_job(conn, job_id: str, batch_id: str, source_id: str) -> None:
    conn.execute(
        """
        insert into jobs(id, workspace_id, workflow_key, source_type, source_id, batch_id)
        values (%s, 'ws-run', 'wf_demo', 'question', %s, %s)
        """,
        (job_id, source_id, batch_id),
    )


def _run_row(conn, run_id: str) -> dict:
    row = conn.execute("select * from runs where id=%s", (run_id,)).fetchone()
    assert row is not None
    return dict(row)


def _job_row(conn, job_id: str) -> dict:
    row = conn.execute("select * from jobs where id=%s", (job_id,)).fetchone()
    assert row is not None
    return dict(row)


@pytest.mark.fresh_schema
def test_v52_database_upgrades_via_init_db() -> None:
    """v52 → v53: every legacy payload shape lands on the run/job columns."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        _rebuild_v52_shape(conn)
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-run', 'runs-ws', 'wf_demo')"
        )
        # Direct intake batch: frozen config + pins + two matched candidates.
        _seed_batch(
            conn,
            "b-direct",
            {
                "entity": "question",
                "node_config": {"n1": {"alpha": 1}},
                "node_code_versions": {"n1": {"version": 3}},
                "task_candidates": [
                    {"entity_id": "Q1", "entity_type": "question", "title": "T1", "stem": "s1"},
                    {"entity_id": "Q2", "entity_type": "question", "title": "T2"},
                ],
            },
        )
        _seed_job(conn, "j-q1", "b-direct", "Q1")
        _seed_job(conn, "j-q2", "b-direct", "Q2")
        # A job whose candidate is missing from the payload (legacy row).
        _seed_job(conn, "j-q3", "b-direct", "Q3")
        # Quality replay batch: replay marker + agent pin must survive.
        _seed_batch(
            conn,
            "b-replay",
            {
                "quality_replay": {"replay_id": "r1", "item_id": "i1", "source_job_id": "j-q1"},
                "node_config": {"n1": {"x": 2}},
                "node_code_versions": {"n1": {"version": 1}},
                "agent_versions": {"n1": {"agent_id": "a", "version": 2, "definition_hash": "h"}},
                "task_candidates": [],
            },
        )
        _seed_job(conn, "j-replay", "b-replay", "replay-r1")
        # Queued async intake batch: the working state must keep driving the
        # consumer, so the whole payload moves to queue_payload_json.
        queued_payload = {
            "entity": "question",
            "node_config": {"n1": {}},
            "node_code_versions": {},
            "task_candidates": [],
            "_intake_queue": {"input_values": ["A", "B"], "next_index": 0},
        }
        _seed_batch(conn, "b-queued", queued_payload, status="queued")
        # Opaque/corrupt payloads degrade to empty pins and a legacy input.
        _seed_batch(conn, "b-empty", {})
        _seed_job(conn, "j-empty", "b-empty", "Q9")
        _seed_batch(conn, "b-corrupt", "not-json{")
        _seed_job(conn, "j-corrupt", "b-corrupt", "Q10")

    init_db(TEST_DATABASE_URL)

    with read_connection(TEST_DATABASE_URL) as conn:
        assert conn.execute("select to_regclass('job_batches')").fetchone()["to_regclass"] is None

        direct = _run_row(conn, "b-direct")
        assert direct["status"] == "completed"
        assert _json(direct["frozen_pins_json"]) == {"node_code_versions": {"n1": {"version": 3}}}
        assert direct["queue_payload_json"] == ""
        q1 = _job_row(conn, "j-q1")
        assert q1["run_id"] == "b-direct"
        assert _json(q1["frozen_config_json"]) == {"n1": {"alpha": 1}}
        assert _json(q1["input_json"]) == {
            "type": "ref",
            "connection_key": "",
            "external_id": "Q1",
            "legacy": True,
            "entity_type": "question",
            "title": "T1",
            "stem": "s1",
        }
        q2 = _job_row(conn, "j-q2")
        assert _json(q2["input_json"])["title"] == "T2"
        assert "stem" not in _json(q2["input_json"])
        # Unmatched candidate: minimal legacy marker, batch config still sinks.
        q3 = _job_row(conn, "j-q3")
        assert _json(q3["input_json"]) == {
            "type": "ref",
            "connection_key": "",
            "external_id": "Q3",
            "legacy": True,
        }
        assert _json(q3["frozen_config_json"]) == {"n1": {"alpha": 1}}

        replay = _run_row(conn, "b-replay")
        assert _json(replay["frozen_pins_json"]) == {
            "quality_replay": {"replay_id": "r1", "item_id": "i1", "source_job_id": "j-q1"},
            "node_code_versions": {"n1": {"version": 1}},
            "agent_versions": {"n1": {"agent_id": "a", "version": 2, "definition_hash": "h"}},
        }
        assert _json(_job_row(conn, "j-replay")["frozen_config_json"]) == {"n1": {"x": 2}}

        queued = _run_row(conn, "b-queued")
        assert queued["status"] == "queued"
        assert _json(queued["queue_payload_json"])["_intake_queue"] == {
            "input_values": ["A", "B"],
            "next_index": 0,
        }
        assert _json(queued["frozen_pins_json"]) == {"node_code_versions": {}}

        assert _json(_run_row(conn, "b-empty")["frozen_pins_json"]) == {}
        assert _json(_job_row(conn, "j-empty")["input_json"])["legacy"] is True
        assert _job_row(conn, "j-empty")["frozen_config_json"] is None
        assert _json(_run_row(conn, "b-corrupt")["frozen_pins_json"]) == {}
        assert _json(_job_row(conn, "j-corrupt")["input_json"])["external_id"] == "Q10"

        migration = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert migration["name"] == "material_bundles"


@pytest.mark.fresh_schema
def test_migrate_runs_is_reentrant() -> None:
    """Replaying migrate_runs after the upgrade is a no-op (idempotent)."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        _rebuild_v52_shape(conn)
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-run', 'runs-ws', 'wf_demo')"
        )
        _seed_batch(
            conn,
            "b-once",
            {
                "node_config": {"n1": {"k": "v"}},
                "task_candidates": [{"entity_id": "Q1", "entity_type": "question", "title": "T1"}],
            },
        )
        _seed_job(conn, "j-once", "b-once", "Q1")
        migrate_runs(conn)
        first = dict(conn.execute("select * from runs where id='b-once'").fetchone())
        first_job = dict(conn.execute("select * from jobs where id='j-once'").fetchone())
        migrate_runs(conn)
        second = dict(conn.execute("select * from runs where id='b-once'").fetchone())
        second_job = dict(conn.execute("select * from jobs where id='j-once'").fetchone())
    assert first == second
    assert first_job == second_job
    assert _json(second_job["input_json"])["external_id"] == "Q1"


@pytest.mark.fresh_schema
def test_migrate_runs_set_based_edge_shapes() -> None:
    """Set-based backfill keeps the old per-row semantics on edge shapes.

    Duplicate ``entity_id`` candidates keep the last occurrence (dict
    overwrite), a non-object JSON payload degrades like the corrupt case, and
    jobs already carrying ``input_json`` / ``frozen_config_json`` are left
    untouched (the ``is null`` guards are the re-entrancy contract).
    """
    with write_transaction(TEST_DATABASE_URL) as conn:
        _rebuild_v52_shape(conn)
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-run', 'runs-ws', 'wf_demo')"
        )
        _seed_batch(
            conn,
            "b-dup",
            {
                "node_config": {"n1": {"k": "v"}},
                "task_candidates": [
                    {"entity_id": "Q1", "entity_type": "question", "title": "first"},
                    {"entity_id": "Q1", "entity_type": "question", "title": "last"},
                ],
            },
        )
        _seed_job(conn, "j-dup", "b-dup", "Q1")
        _seed_batch(conn, "b-array", "[1, 2]")
        _seed_job(conn, "j-array", "b-array", "Q11")
        # A job that already carries freeze columns must keep them.
        _seed_job(conn, "j-preset", "b-dup", "Q2")
        conn.execute("alter table jobs add column if not exists input_json text")
        conn.execute("alter table jobs add column if not exists frozen_config_json text")
        conn.execute(
            "update jobs set input_json=%s, frozen_config_json=%s where id='j-preset'",
            (
                json.dumps({"type": "text", "text": "keep me"}),
                json.dumps({"n1": {"preset": True}}),
            ),
        )
        migrate_runs(conn)
        dup = _job_row(conn, "j-dup")
        assert _json(dup["input_json"])["title"] == "last"
        assert _json(_run_row(conn, "b-array")["frozen_pins_json"]) == {}
        assert _json(_job_row(conn, "j-array")["input_json"]) == {
            "type": "ref",
            "connection_key": "",
            "external_id": "Q11",
            "legacy": True,
        }
        preset = _job_row(conn, "j-preset")
        assert _json(preset["input_json"]) == {"type": "text", "text": "keep me"}
        assert _json(preset["frozen_config_json"]) == {"n1": {"preset": True}}
