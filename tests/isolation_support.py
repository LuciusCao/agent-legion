"""Per-test TRUNCATE isolation machinery for the postgres test tier.

The fixtures in tests/conftest.py drive this module: per test, the dirty
tables of the per-xdist-worker schema are truncated and the seeded rows
replayed (see conftest._isolate_postgres_database). Split out of conftest
to keep the tests-root files under their line budget. Like
tests/postgres_support, importing this module must stay side-effect free —
nothing here touches the database at import time.
"""

from __future__ import annotations

import json

import psycopg
import pytest
from psycopg import sql

from tests.postgres_support import BASE_DATABASE_URL, TEST_SCHEMA

# Deterministic pricing seeded into global_settings after every TRUNCATE (see
# reset_schema_data); rates mirror the retired yaml defaults so historical
# cost assertions stay valid.
_TEST_PRICING_DOCUMENT = {
    "currency": "CNY",
    "pricing": [
        {
            "provider": "gateway",
            "model": "your-model-a",
            "input_per_1m": 3.0,
            "output_per_1m": 15.0,
            "cache_read_per_1m": 0.6,
        },
        {
            "provider": "doubao",
            "model": "Doubao-Seed-2.1-turbo",
            "input_per_1m": 3.0,
            "output_per_1m": 15.0,
            "cache_read_per_1m": 0.6,
        },
        {
            "provider": "gateway",
            "model": "your-model-b",
            "input_per_1m": 1.0,
            "output_per_1m": 2.0,
            "cache_read_per_1m": 0.2,
        },
        {
            "provider": "deepseek",
            "model": "your-model-b",
            "input_per_1m": 1.0,
            "output_per_1m": 2.0,
            "cache_read_per_1m": 0.2,
        },
    ],
}

# Tables re-seeded after every reset (see conftest._isolate_postgres_database).
# After the first full service-layer seed of a session their rows are
# snapshotted; later resets replay the snapshot with plain multi-row INSERTs
# instead of re-running the service-layer seed (~70ms/test) per test. The
# snapshot is invalidated by every schema rebuild, so DDL drift can never
# stale it.
#
# Note: the replayed rows are byte-frozen at capture time — timestamps inside
# seed rows do NOT advance between tests. A test asserting a seeded row is
# "fresh" (e.g. updated_at >= now - interval) would false-red; assert
# presence/content, never recency, against seeded rows.
_SEEDED_TABLES = ("job_event_seq", "global_settings", "versioned_entities")
_SEED_SNAPSHOT: dict[str, tuple[list[str], list[tuple]]] | None = None
# Cached pg_tables listing for the worker schema, same lifecycle as the seed
# snapshot (both are invalidated by every schema rebuild). Tests that change
# DDL must opt into fresh_schema, so the listing is stable inside a non-fresh
# session. The dirty probe never blindly trusts it: every probe re-counts
# pg_tables server-side, and a statement error on a phantom (dropped) name
# also marks the listing untrusted — either way the listing is refetched and
# the probe retried once against fresh data, so drift can never hide a dirty
# table (only a repeated failure falls back to "everything dirty").
_TABLE_LIST: list[str] | None = None

# Session-lifetime maintenance connection for per-test isolation. Reusing one
# connection removes a fresh connect handshake from every test and lets
# session-level settings be applied once:
# - lock_timeout bounds every lock wait of the isolation pass: the TRUNCATE
#   needs ACCESS EXCLUSIVE on every dirty table, and the dirty-probe EXISTS
#   reads wait behind row locks — a leaked open transaction (observed shape:
#   a TestClient anyio-threadpool request leaves `update agent_workers` open
#   on a pooled connection) would otherwise hang both forever. The timeout
#   turns that hang into a failure that names the blocking session (#1045).
# - synchronous_commit=off drops the WAL-flush wait from the reset's commits.
#   Profiling an 8-worker run showed the TRUNCATE stage dominating isolation
#   cost with no lock queue behind it — the wait is the fsync=on flush, and
#   durability of a scratch schema the next session rebuilds from DDL is
#   irrelevant. Commit atomicity and visibility are unchanged.
_RESET_CONN = None


def reset_connection():
    global _RESET_CONN
    if _RESET_CONN is None or _RESET_CONN.closed:
        _RESET_CONN = psycopg.connect(BASE_DATABASE_URL, autocommit=True)
        _RESET_CONN.execute("set lock_timeout = '30s'")
        _RESET_CONN.execute("set synchronous_commit = 'off'")
    return _RESET_CONN


def close_reset_connection() -> None:
    global _RESET_CONN
    if _RESET_CONN is not None:
        _RESET_CONN.close()
        _RESET_CONN = None


def invalidate_reset_state() -> None:
    """Drop every cached view of the schema (called on every schema rebuild)."""
    global _SEED_SNAPSHOT, _TABLE_LIST
    _SEED_SNAPSHOT = None
    _TABLE_LIST = None


def capture_seed_snapshot() -> None:
    global _SEED_SNAPSHOT
    snapshot: dict[str, tuple[list[str], list[tuple]]] = {}
    with psycopg.connect(BASE_DATABASE_URL, autocommit=True) as conn:
        for table in _SEEDED_TABLES:
            cursor = conn.execute(
                sql.SQL("select * from {}").format(sql.Identifier(TEST_SCHEMA, table))
            )
            columns = [col.name for col in cursor.description]
            snapshot[table] = (columns, cursor.fetchall())
    _SEED_SNAPSHOT = snapshot


def _restore_seed_rows(conn, tables: list[str]) -> None:
    for table in tables:
        columns, rows = _SEED_SNAPSHOT[table]
        if not rows:
            continue
        row_sql = (
            sql.SQL("(") + sql.SQL(", ").join(sql.Placeholder() for _ in columns) + sql.SQL(")")
        )
        conn.execute(
            sql.SQL("insert into {} ({}) values {}").format(
                sql.Identifier(TEST_SCHEMA, table),
                sql.SQL(", ").join(sql.Identifier(c) for c in columns),
                sql.SQL(", ").join(row_sql for _ in rows),
            ),
            [value for row in rows for value in row],
        )


def _list_tables(conn) -> list[str]:
    """Fresh pg_tables listing for the worker schema (minus schema_migrations)."""
    return [
        row[0]
        for row in conn.execute(
            "select tablename from pg_tables where schemaname = %s", (TEST_SCHEMA,)
        ).fetchall()
        if row[0] != "schema_migrations"
    ]


def _dirty_tables(conn, tables: list[str]) -> set[str] | None:
    """Tables holding rows or owning an advanced identity sequence.

    Row existence is an exact per-table EXISTS probe, not a stats-estimator
    read, so a freshly written table can never be misjudged as clean.
    Sequence state matters because a table can be empty while its identity
    sequence advanced (rows inserted, then deleted); only TRUNCATE
    ... RESTART IDENTITY rewinds that, so such tables stay in the truncate
    set.

    One round trip carries the row probes, the sequence probe, and a
    staleness guard: the server-side pg_tables count must match the cached
    ``tables`` listing (+1 for schema_migrations, which the listing
    excludes). The probe returns None — untrusted listing, not a guess —
    on a count mismatch (DDL happened without a schema rebuild) or on a
    statement error (the listing names a dropped table, e.g. same-count
    create+drop DDL the count guard cannot see). The caller then refetches
    the listing and re-probes; only a repeated failure falls back to
    "everything dirty". Missing a dirty table would leak data between
    tests, which is worse than slow. LockNotAvailable is deliberately NOT
    mapped to None: it propagates to the #1045 blocker attribution
    (_fail_on_leaked_locks) instead of being retried into a second 30s
    wait.

    Precondition: every sequence in the test schema is column-owned (serial /
    identity / owned default), so the pg_depend auto/internal join below
    reaches it. A standalone CREATE SEQUENCE (no owning column) is invisible
    here; the current schema has none — if one is ever added, it must be
    rewound explicitly in reset_schema_data.
    """
    probes = sql.SQL(", ").join(
        sql.SQL("exists(select 1 from {}) as {}").format(
            sql.Identifier(TEST_SCHEMA, table), sql.Identifier(table)
        )
        for table in tables
    )
    query = sql.SQL(
        "select"
        " (select count(*) from pg_tables where schemaname = {schema}) as table_count,"
        " coalesce((select array_agg(t.relname::text)"
        "   from pg_class s"
        "   join pg_namespace n on n.oid = s.relnamespace"
        "   join pg_depend d on d.objid = s.oid and d.deptype in ('a', 'i')"
        "   join pg_class t on t.oid = d.refobjid"
        "   join pg_sequences ps"
        "     on ps.schemaname = n.nspname and ps.sequencename = s.relname"
        "   where s.relkind = 'S' and n.nspname = {schema}"
        "     and ps.last_value is not null), array[]::text[]) as seq_dirty,"
        " {probes}"
    ).format(schema=sql.Literal(TEST_SCHEMA), probes=probes)
    try:
        row = conn.execute(query).fetchone()
    except psycopg.errors.LockNotAvailable:
        raise
    except psycopg.Error:
        return None
    if row[0] != len(tables) + 1:
        return None
    dirty = {table for table, has_rows in zip(tables, row[2:], strict=True) if has_rows}
    dirty.update(row[1])
    return dirty


def _fail_on_leaked_locks(conn, phase: str) -> None:
    """Fail with attribution when the isolation pass hits the lock timeout.

    The blocker query must not itself inherit the lock_timeout wait: it
    reads only pg_locks / pg_stat_activity (catalog), so it returns
    immediately. It lists sessions HOLDING locks on this worker's schema,
    not pg_blocking_pids(): by the time it runs the timed-out statement was
    cancelled, nobody waits any more, and the waiter-based probe always
    answered "(none visible)" (#1045).
    """
    blockers = conn.execute(
        """
        select distinct a.pid, a.state, left(a.query, 90) as query
        from pg_locks l
        join pg_class c on c.oid = l.relation
        join pg_namespace n on n.oid = c.relnamespace
        join pg_stat_activity a on a.pid = l.pid
        where n.nspname = %s and l.granted and l.pid <> pg_backend_pid()
        """,
        (TEST_SCHEMA,),
    ).fetchall()
    held = "\n".join(f"  pid {row[0]} ({row[1]}): {row[2]}" for row in blockers)
    pytest.fail(
        f"Test-schema isolation {phase} timed out on a lock wait after 30s — another "
        "session holds locks on this schema (a leaked open transaction, e.g. a "
        "request thread that never committed). Blocking sessions:\n"
        f"{held or '  (none visible)'}"
    )


def reset_schema_data() -> bool:
    """Empty dirty tables without touching DDL, then restore seeded rows.

    Returns True when the reset replayed the seed snapshot (seeded tables
    restored inline); False when a full service-layer seed must run (first
    reset after a schema build, snapshot not captured yet).

    Only tables that actually hold rows (or own an advanced identity
    sequence) are truncated; clean tables are left alone. Seeded tables are
    effectively always dirty, so their per-test restoration is certain; the
    seeded content itself is bit-identical to the service-layer seed because
    the snapshot was captured from it. schema_migrations keeps its rows: it
    is constant after init_db, and tests that re-run init_db rely on it for
    idempotency.

    The pass runs on the session maintenance connection (see
    reset_connection) in two transaction segments: the listing/probe reads
    run as standalone autocommit statements, then the TRUNCATE + seed
    replay run inside one pipeline (psycopg wraps an autocommit pipeline
    in a single implicit transaction) so the writes cost a single commit.
    The split is a correctness requirement, not just grouping: inside one
    implicit transaction the server discards every command queued behind a
    failed statement, so a probe error sharing the transaction would
    silently drop the all-dirty fallback TRUNCATE. A replay error still
    rolls the TRUNCATE back instead of leaving empty tables behind, and
    either way the next test's probe finds the same tables dirty and
    resets them again.
    """
    global _TABLE_LIST
    try:
        conn = reset_connection()
        if _SEED_SNAPSHOT is None or _TABLE_LIST is None:
            tables = _list_tables(conn)
            _TABLE_LIST = tables
            dirty = set(tables)
        else:
            try:
                dirty = _dirty_tables(conn, _TABLE_LIST)
            except psycopg.errors.LockNotAvailable:
                _fail_on_leaked_locks(conn, "dirty-table probe")
            if dirty is None:
                # Untrusted cached listing (count guard tripped or a
                # statement error on a phantom name): refetch and re-probe
                # once against the fresh listing; only a repeated failure
                # falls back to "everything dirty". The fresh listing
                # replaces the cache either way, so a poisoned cache
                # self-heals instead of failing every reset for the rest of
                # the session.
                tables = _list_tables(conn)
                try:
                    dirty = _dirty_tables(conn, tables)
                except psycopg.errors.LockNotAvailable:
                    _fail_on_leaked_locks(conn, "dirty-table probe")
                _TABLE_LIST = tables
                if dirty is None:
                    dirty = set(tables)
            # Seeded tables are always re-truncated and replayed: a test
            # that deleted seed rows without adding new ones would
            # otherwise look "clean" to the row probe and lose its seeds.
            dirty.update(t for t in _SEEDED_TABLES if t in _TABLE_LIST)
        try:
            with conn.pipeline():
                if dirty:
                    conn.execute(
                        sql.SQL("truncate {} restart identity cascade").format(
                            sql.SQL(", ").join(sql.Identifier(TEST_SCHEMA, t) for t in dirty)
                        )
                    )
                if _SEED_SNAPSHOT is not None:
                    _restore_seed_rows(conn, [t for t in _SEEDED_TABLES if t in dirty])
        except psycopg.errors.LockNotAvailable:
            # TRUNCATE and the replay INSERTs share the pipeline, so a lock
            # wait on either surfaces here at pipeline exit — the phase
            # names both, not just the TRUNCATE.
            _fail_on_leaked_locks(conn, "TRUNCATE + seed replay")
        if _SEED_SNAPSHOT is not None:
            return True
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests. Set AGENT_LEGION_TEST_DATABASE_URL to a reachable "
            f"test database: {exc}"
        )
    # First reset after a (re)build: keep the historical full-seed path. The
    # job_event_seq singleton counter row (postgres_schema.sql) is bumped by
    # job intake on every batch, and global_settings gets a fixed token_usage
    # pricing document so cost-calculation tests have deterministic rates.
    try:
        with psycopg.connect(BASE_DATABASE_URL, autocommit=True) as conn:
            conn.execute(
                sql.SQL(
                    "insert into {}(id, value) values (1, 0) on conflict(id) do nothing"
                ).format(sql.Identifier(TEST_SCHEMA, "job_event_seq"))
            )
            conn.execute(
                sql.SQL(
                    "insert into {}(key, value) values ('token_usage', %s)"
                    " on conflict(key) do update set value=excluded.value"
                ).format(sql.Identifier(TEST_SCHEMA, "global_settings")),
                (json.dumps(_TEST_PRICING_DOCUMENT),),
            )
    except psycopg.Error as exc:
        pytest.fail(
            "PostgreSQL is required for tests. Set AGENT_LEGION_TEST_DATABASE_URL to a reachable "
            f"test database: {exc}"
        )
    return False
