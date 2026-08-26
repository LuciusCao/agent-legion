"""Per-version migration recording and incremental upgrade semantics.

init_db used to record a single latest-version row and replay every data
migration on any upgrade; the version registry now records one row per
migration and only runs versions above ``max(applied)``. These tests pin
the three load-bearing behaviors: fresh install records every version,
upgrades run only the increment, and repeated init_db is a no-op.
"""

from __future__ import annotations

import pytest

from server.app.db.migration_registry import MIGRATIONS
from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.fresh_schema


def test_fresh_install_records_every_registered_version() -> None:
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = {
            row["version"]: row["name"]
            for row in conn.execute("select version, name from schema_migrations").fetchall()
        }
    assert rows == {m.version: m.name for m in MIGRATIONS}


def test_registry_is_version_sorted_and_current() -> None:
    versions = [m.version for m in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))
    assert versions[-1] == SCHEMA_VERSION


def test_repeated_init_db_is_a_noop() -> None:
    # Second init_db on an up-to-date database must not touch the table (the
    # early return happens before any DDL replay or migration insert).
    init_db(TEST_DATABASE_URL)
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = {
            row["version"]: row["name"]
            for row in conn.execute("select version, name from schema_migrations").fetchall()
        }
    assert rows == {m.version: m.name for m in MIGRATIONS}


def test_upgrade_from_older_version_runs_only_the_increment() -> None:
    # Simulate a database that stopped at v47: drop the later rows and the
    # artifacts they created, then init_db must run only v50+ migrations.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version > 47")
        # Drop the runs table so the v53 cutover migration has visible work;
        # its idempotent DDL create comes from the schema file replay.
        conn.execute("drop table if exists runs")

    init_db(TEST_DATABASE_URL)

    with read_connection(TEST_DATABASE_URL) as conn:
        rows = {
            row["version"]: row["name"]
            for row in conn.execute("select version, name from schema_migrations").fetchall()
        }
    assert rows == {m.version: m.name for m in MIGRATIONS}
    # The increment actually ran: the v53 runs table exists again.
    with read_connection(TEST_DATABASE_URL) as conn:
        exists = conn.execute("select to_regclass('runs') as oid").fetchone()
    assert exists is not None and exists["oid"] is not None


def test_legacy_single_row_upgrade_runs_only_the_increment() -> None:
    # Legacy installs recorded a single latest-version row. Upgrading such a
    # database must run only versions above that watermark — never replay the
    # retired executor data migrations (v24-v47), which a membership check
    # would re-run against the current schema.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations")
        conn.execute(
            "insert into schema_migrations(version, name) values (%s, %s)",
            (SCHEMA_VERSION - 1, "legacy_single_row"),
        )

    init_db(TEST_DATABASE_URL)

    with read_connection(TEST_DATABASE_URL) as conn:
        rows = {
            row["version"]: row["name"]
            for row in conn.execute("select version, name from schema_migrations").fetchall()
        }
    assert rows == {
        SCHEMA_VERSION - 1: "legacy_single_row",
        SCHEMA_VERSION: MIGRATIONS[-1].name,
    }
