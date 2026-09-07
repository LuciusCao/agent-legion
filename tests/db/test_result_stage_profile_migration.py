"""Schema v80: result-stage gauge columns on ops_runtime_profile_samples (#521).

The result-commit stage split (unpack / artifacts_verify / validate /
artifacts_upload / lease_write / events / mark_done — see
``server.app.agent_broker.result_timing``) persists through the #359
runtime-profile sampler; this file pins the migration side: fresh installs
and upgrades both land the fourteen columns, the sampler round-trips them,
and the chain tail is v80.
"""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.runtime_profile import (
    RuntimeProfile,
    persist_profile_sample,
    query_profile_series,
)
from tests.postgres_support import TEST_DATABASE_URL

_STAGE_COLUMNS = (
    "result_unpack_seconds_total",
    "result_unpack_seconds_max",
    "result_artifacts_verify_seconds_total",
    "result_artifacts_verify_seconds_max",
    "result_validate_seconds_total",
    "result_validate_seconds_max",
    "result_artifacts_upload_seconds_total",
    "result_artifacts_upload_seconds_max",
    "result_lease_write_seconds_total",
    "result_lease_write_seconds_max",
    "result_events_seconds_total",
    "result_events_seconds_max",
    "result_mark_done_seconds_total",
    "result_mark_done_seconds_max",
)

_BUCKET = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)


def _existing_columns(conn) -> set[str]:
    rows = conn.execute(
        "select column_name from information_schema.columns"
        " where table_schema=current_schema() and table_name='ops_runtime_profile_samples'"
    ).fetchall()
    return {str(row["column_name"]) for row in rows}


def _bucket_row() -> dict:
    series = query_profile_series(TEST_DATABASE_URL, buckets=5)
    return next(item for item in series if str(item["bucket_start"]).startswith("2026-09-07T12:00"))


def test_fresh_schema_has_stage_columns() -> None:
    # The autouse fixture already ran init_db at SCHEMA_VERSION.
    assert SCHEMA_VERSION == 80
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = _existing_columns(conn)
    for column in _STAGE_COLUMNS:
        assert column in columns, column


def test_stage_columns_round_trip_through_the_sampler() -> None:
    # persist_profile_sample must carry the stage deltas into the bucket row
    # (the queries-layer column list and the sampling merge are the two
    # places a new column can be dropped silently).
    profile = RuntimeProfile()
    profile.note_result_stages(
        {"unpack": 0.1, "artifacts_verify": 0.4, "validate": 0.02, "spool": 5.0}
    )
    persist_profile_sample(
        TEST_DATABASE_URL,
        _BUCKET,
        profile,
        queued_depth=0,
        active_executions=0,
        enqueue_pending=0,
    )
    row = _bucket_row()
    assert row["result_unpack_seconds_total"] == 0.1
    assert row["result_unpack_seconds_max"] == 0.1
    assert row["result_artifacts_verify_seconds_total"] == 0.4
    assert row["result_validate_seconds_total"] == 0.02
    assert row["result_events_seconds_total"] == 0.0


def test_upgrade_from_v79_adds_the_columns() -> None:
    # A database recorded at v79 replays the schema file (CREATE TABLE IF NOT
    # EXISTS is a no-op) and runs the v80 migration: the guarded ALTERs are
    # the only path that widens the existing table.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version >= 80")
        for column in _STAGE_COLUMNS:
            conn.execute(
                psycopg.sql.SQL(
                    "alter table ops_runtime_profile_samples drop column if exists {}"
                ).format(psycopg.sql.Identifier(column))
            )
    init_db(TEST_DATABASE_URL)
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = _existing_columns(conn)
        row = conn.execute("select name from schema_migrations where version=%s", (80,)).fetchone()
    assert row is not None and row["name"] == "result_stage_profile"
    for column in _STAGE_COLUMNS:
        assert column in columns, column
