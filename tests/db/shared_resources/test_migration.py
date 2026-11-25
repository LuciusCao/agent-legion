from __future__ import annotations

import pytest

from server.app.db.schema import SCHEMA_VERSION, init_db

_TABLES = (
    "shared_resource_job_pins",
    "shared_resource_selections",
    "shared_resource_files",
    "shared_resource_grants",
    "shared_resource_versions",
    "shared_resources",
)


@pytest.mark.fresh_schema
def test_v106_upgrade_creates_the_complete_shared_resource_schema(job_db) -> None:
    with job_db.write() as conn:
        for table in _TABLES:
            conn.execute(f"drop table if exists {table}")
        conn.execute("drop function if exists shared_resource_ready_files_immutable()")
        conn.execute("drop function if exists shared_resource_ready_version_immutable()")
        conn.execute("delete from schema_migrations where version=%s", (SCHEMA_VERSION,))

    init_db(job_db.dsn_identity)

    with job_db.read() as conn:
        rows = conn.execute(
            "select table_name from information_schema.tables where table_schema=current_schema()"
            " and table_name like 'shared_resource%' order by table_name"
        ).fetchall()
        migration = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
        triggers = conn.execute(
            "select tgname from pg_trigger where tgname like 'shared_resource%' order by tgname"
        ).fetchall()
    assert {row["table_name"] for row in rows} == set(_TABLES)
    assert migration["name"] == "shared_resources"
    assert {row["tgname"] for row in triggers} == {
        "shared_resource_files_immutable",
        "shared_resource_versions_immutable",
    }
