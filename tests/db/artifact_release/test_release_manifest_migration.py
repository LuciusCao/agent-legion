from __future__ import annotations

import hashlib
import json

import psycopg
import pytest

from server.app.db.migrations.release_manifest import migrate_release_manifest
from server.app.db.schema import SCHEMA_VERSION, init_db

_BASE_NAMES = ["metadata.json", "report.md", "result.json"]
_ALIAS_OUTPUTS = [
    "reports/final.json",
    "reports//final.json",
    "./result.json",
    "result.json",
    "reports/./audit.json",
    "reports//audit.json",
    "z.json",
    "z.json",
]
_EXPECTED_CANONICAL_NAMES = sorted(
    _BASE_NAMES + ["reports/audit.json", "reports/final.json", "z.json"]
)


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


def _rewind_v108(conn) -> None:
    conn.execute("drop table workspace_release_manifests")
    conn.execute("drop function workspace_release_manifest_immutable()")
    conn.execute("delete from schema_migrations where version >= 108")


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
        _rewind_v108(conn)
        conn.execute(
            "insert into workspaces(id, name) values ('legacy-ws', 'Legacy'),"
            " ('no-active-ws', 'No active')"
        )
        _insert_revision(conn, "legacy-ws", "legacy-active", "active", _definition(_ALIAS_OUTPUTS))
        _insert_revision(
            conn,
            "legacy-ws",
            "legacy-archived",
            "archived",
            _definition(["archived-only.json"]),
            version=2,
        )

    init_db(job_db.dsn_identity)

    expected_document = json.dumps(_EXPECTED_CANONICAL_NAMES, separators=(",", ":"))
    expected_hash = hashlib.sha256(expected_document.encode("utf-8")).hexdigest()
    with job_db.read() as conn:
        rows = _rows(conn)
        assert [row["workspace_id"] for row in rows] == ["legacy-ws", "no-active-ws"]
        assert json.loads(rows[0]["artifact_names_json"]) == _EXPECTED_CANONICAL_NAMES
        assert rows[0]["artifact_names_json"] == expected_document
        assert rows[0]["manifest_hash"] == expected_hash
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
        assert rows[0]["artifact_names_json"] == expected_document
        assert rows[0]["manifest_hash"] == expected_hash


@pytest.mark.fresh_schema
@pytest.mark.parametrize(
    "bad_name",
    [
        "../escape.json",
        "/absolute.json",
        "runs/result.json",
        ".hidden/result.json",
        "reports/.staging/result.json",
        "bad\nname.json",
        "reports\\result.json",
    ],
)
def test_v107_upgrade_fails_closed_for_non_downloadable_outputs(job_db, bad_name: str) -> None:
    with job_db.write() as conn:
        _rewind_v108(conn)
        conn.execute("insert into workspaces(id, name) values ('legacy-ws', 'Legacy')")
        original = _definition([bad_name])
        _insert_revision(conn, "legacy-ws", "legacy-active", "active", original)

    with pytest.raises(ValueError) as caught:
        init_db(job_db.dsn_identity)

    assert "legacy-ws" in str(caught.value)
    assert repr(bad_name) in str(caught.value)
    assert "correct the active revision declaration" in str(caught.value)
    with job_db.read() as conn:
        table = conn.execute("select to_regclass('workspace_release_manifests') as name").fetchone()
        migration = conn.execute("select name from schema_migrations where version=108").fetchone()
        active = conn.execute(
            "select definition_json from workflow_revisions where id='legacy-active'"
        ).fetchone()
    assert table["name"] is None
    assert migration is None
    assert active["definition_json"] == original


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
