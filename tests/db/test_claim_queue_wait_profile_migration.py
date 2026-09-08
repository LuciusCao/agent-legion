"""Schema v81: claim queue-wait gauge columns on ops_runtime_profile_samples (#551).

The supply-side wait (queued_at → promote, folded by ``evaluate_candidate``
per promote into the claim gauge family) persists through the #359 sampler;
this file pins the migration side: fresh installs and upgrades both land the
two columns, the sampler round-trips them, and the chain tail is v81.
Mirrors test_result_stage_profile_migration.py (v80).
"""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.runtime_profile import (
    RuntimeProfile,
    persist_profile_sample,
    query_profile_series,
)
from tests.postgres_support import TEST_DATABASE_URL

_QUEUE_WAIT_COLUMNS = (
    "claim_queue_wait_seconds_total",
    "claim_queue_wait_seconds_max",
)

_BUCKET = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)


def _existing_columns(conn) -> set[str]:
    rows = conn.execute(
        "select column_name from information_schema.columns"
        " where table_schema=current_schema() and table_name='ops_runtime_profile_samples'"
    ).fetchall()
    return {str(row["column_name"]) for row in rows}


def _bucket_row() -> dict:
    series = query_profile_series(TEST_DATABASE_URL, buckets=5)
    return next(item for item in series if str(item["bucket_start"]).startswith("2026-09-08T12:00"))


def test_fresh_schema_has_queue_wait_columns() -> None:
    # The autouse fixture already ran init_db at SCHEMA_VERSION.
    assert SCHEMA_VERSION == 81
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = _existing_columns(conn)
    for column in _QUEUE_WAIT_COLUMNS:
        assert column in columns, column


def test_queue_wait_round_trips_through_the_sampler() -> None:
    # persist_profile_sample must carry the queue_wait delta into the bucket
    # row (the queries-layer column list and the sampling merge are the two
    # places a new gauge can be dropped silently).
    profile = RuntimeProfile()
    profile.note_claim_stages({"queue_wait": 7.5, "scan": 0.03})
    persist_profile_sample(
        TEST_DATABASE_URL,
        _BUCKET,
        profile,
        queued_depth=0,
        active_executions=0,
        enqueue_pending=0,
    )
    row = _bucket_row()
    assert row["claim_queue_wait_seconds_total"] == 7.5
    assert row["claim_queue_wait_seconds_max"] == 7.5
    assert row["claim_scan_seconds_total"] == 0.03


@pytest.mark.fresh_schema
def test_upgrade_from_v80_adds_the_columns() -> None:
    # A database recorded at v80 replays the schema file (no-op) and runs the
    # v81 migration: the guarded ALTERs are the only widening path.
    # fresh_schema because the test drops columns (DDL drift must not leak
    # into later tests on this worker — same discipline as the v80 test).
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version >= 81")
        for column in _QUEUE_WAIT_COLUMNS:
            conn.execute(
                psycopg.sql.SQL(
                    "alter table ops_runtime_profile_samples drop column if exists {}"
                ).format(psycopg.sql.Identifier(column))
            )
    init_db(TEST_DATABASE_URL)
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = _existing_columns(conn)
        row = conn.execute("select name from schema_migrations where version=%s", (81,)).fetchone()
    assert row is not None and row["name"] == "claim_queue_wait_profile"
    for column in _QUEUE_WAIT_COLUMNS:
        assert column in columns, column
