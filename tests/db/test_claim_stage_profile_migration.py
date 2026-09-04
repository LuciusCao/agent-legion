"""Schema v78: claim-stage gauge columns on ops_runtime_profile_samples (#448).

Phase 1 of the claim-throughput work instruments the claim transaction with
a stage split (scan / evaluate / writes — see
``server/app.agent_broker.claim_timing``); this file pins the migration
side: fresh installs and upgrades both land the six columns, the sampler
round-trips them, and the chain tail is v78.
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
    "claim_scan_seconds_total",
    "claim_scan_seconds_max",
    "claim_evaluate_seconds_total",
    "claim_evaluate_seconds_max",
    "claim_writes_seconds_total",
    "claim_writes_seconds_max",
)

_BUCKET = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def _existing_columns(conn) -> set[str]:
    rows = conn.execute(
        "select column_name from information_schema.columns"
        " where table_schema=current_schema() and table_name='ops_runtime_profile_samples'"
    ).fetchall()
    return {str(row["column_name"]) for row in rows}


def _bucket_row() -> dict:
    series = query_profile_series(TEST_DATABASE_URL, buckets=5)
    return next(item for item in series if str(item["bucket_start"]).startswith("2026-09-04T12:00"))


def test_fresh_schema_has_stage_columns() -> None:
    # The autouse fixture already ran init_db at SCHEMA_VERSION.
    assert SCHEMA_VERSION == 78
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = _existing_columns(conn)
    for column in _STAGE_COLUMNS:
        assert column in columns, column


def test_stage_columns_round_trip_through_the_sampler() -> None:
    # persist_profile_sample must carry the stage deltas into the bucket row
    # (the queries-layer column list and the sampling merge are the two
    # places a new column can be dropped silently).
    profile = RuntimeProfile()
    profile.note_claim_stages(
        {"worker_setup": 0.01, "scan": 0.2, "evaluate": 0.05, "writes": 0.02, "commit": 0.005},
        claimed=True,
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
    assert row["claim_scan_seconds_total"] == 0.2
    assert row["claim_scan_seconds_max"] == 0.2
    assert row["claim_evaluate_seconds_total"] == 0.05
    assert row["claim_writes_seconds_total"] == 0.02


def test_upgrade_from_v77_adds_the_columns() -> None:
    # A database recorded at v77 replays the schema file (CREATE TABLE IF NOT
    # EXISTS is a no-op) and runs the v78 migration: the guarded ALTERs are
    # the only path that widens the existing table.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version >= 78")
        for column in _STAGE_COLUMNS:
            conn.execute(
                psycopg.sql.SQL(
                    "alter table ops_runtime_profile_samples drop column if exists {}"
                ).format(psycopg.sql.Identifier(column))
            )
    init_db(TEST_DATABASE_URL)
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = _existing_columns(conn)
        row = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert row is not None and row["name"] == "claim_stage_profile"
    for column in _STAGE_COLUMNS:
        assert column in columns, column
