from __future__ import annotations

import json

import psycopg
import pytest

from server.app.db.migrations.release_manifest import migrate_release_manifest
from server.app.db.schema import SCHEMA_VERSION, init_db

_BASE_NAMES = ["metadata.json", "report.md", "result.json"]


def _definition(outputs: list[str]) -> str:
    return json.dumps(
        {
            "key": "legacy-ws",
            "label": "Legacy",
            "schema_version": 2,
            "intake": {"modes": {}},
            "nodes": {
                "start": {"node_type": "start", "label": "Start", "capability": ""},
                "produce": {
                    "node_type": "code",
                    "label": "Produce",
                    "capability": "",
                    "outputs": outputs,
                },
            },
            "edges": [{"source": "start", "target": "produce", "condition": None}],
        },
        separators=(",", ":"),
    )


def _insert_revision(
    conn, workspace_id: str, revision_id: str, status: str, text: str, version: int = 1
) -> None:
    conn.execute(
        "insert into workflow_revisions(id, workspace_id, version, status, definition_json,"
        " definition_hash, created_at, published_at) values"
        " (%s, %s, %s, %s, %s, 'hash', timestamptz '2020-01-01', timestamptz '2020-01-02')",
        (revision_id, workspace_id, version, status, text),
    )


def _rows(conn) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "select * from workspace_release_manifests order by workspace_id, revision"
        ).fetchall()
    ]


def test_fresh_schema_registers_release_manifest(job_db) -> None:
    assert SCHEMA_VERSION == 108
    with job_db.read() as conn:
        migration = conn.execute("select name from schema_migrations where version=108").fetchone()
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema()"
                " and table_name='workspace_release_manifests'"
            ).fetchall()
        }
        trigger = conn.execute(
            "select tgname from pg_trigger where tgname='workspace_release_manifests_immutable'"
        ).fetchone()
        assert _rows(conn) == []
    assert migration["name"] == "release_manifest"
    assert columns == {
        "id",
        "workspace_id",
        "revision",
        "artifact_names_json",
        "manifest_hash",
        "created_by",
        "created_at",
    }
    assert trigger is not None


@pytest.mark.fresh_schema
def test_v107_upgrade_backfills_the_frozen_active_scope_once(job_db) -> None:
    with job_db.write() as conn:
        conn.execute("drop table workspace_release_manifests")
        conn.execute("drop function workspace_release_manifest_immutable()")
        conn.execute("delete from schema_migrations where version >= 108")
        conn.execute(
            "insert into workspaces(id, name) values ('legacy-ws', 'Legacy'),"
            " ('no-active-ws', 'No active')"
        )
        _insert_revision(conn, "legacy-ws", "legacy-active", "active", _definition(["z.json"]))
        _insert_revision(
            conn,
            "legacy-ws",
            "legacy-archived",
            "archived",
            _definition(["archived-only.json"]),
            version=2,
        )

    init_db(job_db.dsn_identity)

    with job_db.read() as conn:
        rows = _rows(conn)
        assert [row["workspace_id"] for row in rows] == ["legacy-ws", "no-active-ws"]
        assert json.loads(rows[0]["artifact_names_json"]) == sorted(_BASE_NAMES + ["z.json"])
        assert json.loads(rows[1]["artifact_names_json"]) == _BASE_NAMES
        assert all(row["revision"] == 1 for row in rows)
        assert all(row["created_by"] == "schema-v108-backfill" for row in rows)
        before = rows[0]

    with job_db.write() as conn:
        conn.execute(
            "update workflow_revisions set definition_json=%s where id='legacy-active'",
            (_definition(["newer-live-output.json"]),),
        )
        migrate_release_manifest(conn)
        conn.execute("insert into workspaces(id, name) values ('created-later', 'Created later')")
    init_db(job_db.dsn_identity)

    with job_db.read() as conn:
        rows = _rows(conn)
        assert len(rows) == 2
        assert rows[0] == before
        assert json.loads(rows[0]["artifact_names_json"]) == sorted(_BASE_NAMES + ["z.json"])


def test_history_rejects_mutation_but_allows_workspace_cascade(job_db) -> None:
    with job_db.write() as conn:
        conn.execute("insert into workspaces(id, name) values ('immutable-ws', 'Immutable')")
        migrate_release_manifest(conn)

    with (
        pytest.raises(psycopg.DatabaseError, match="revisions are immutable"),
        job_db.write() as conn,
    ):
        conn.execute(
            "update workspace_release_manifests set artifact_names_json='[]'"
            " where workspace_id='immutable-ws'"
        )
    with (
        pytest.raises(psycopg.DatabaseError, match="revisions are immutable"),
        job_db.write() as conn,
    ):
        conn.execute("delete from workspace_release_manifests where workspace_id='immutable-ws'")

    with job_db.write() as conn:
        conn.execute("delete from workspaces where id='immutable-ws'")
    with job_db.read() as conn:
        assert _rows(conn) == []
