# PostgreSQL runbook

PostgreSQL is the only runtime database. The API, scheduler replicas, sweepers,
and remote workers coordinate through the same database; there is no SQLite
fallback.

## Local setup

```bash
createdb agent_legion
export VIDEO_HIVE_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion
uv run uvicorn server.app.main:app --reload
```

The server creates the current schema under a PostgreSQL advisory migration
lock. The configured role needs permission to connect and to create/alter
objects in its application schema.

## Import an existing SQLite database

Stop the old application first so the source file cannot change during the
read transaction. Import into a fresh target:

```bash
uv run python scripts/import-sqlite-to-postgres.py \
  data/video_hive.sqlite \
  postgresql://127.0.0.1:5432/agent_legion
```

The importer copies tables in foreign-key order, preserves identity IDs, resets
identity sequences, and refuses a populated target. `--truncate-target` is an
explicit destructive retry option. Retain the SQLite file until the printed row
counts and an application smoke test have been checked.

## Capacity for 200–300 agents

Agent count and database connection count are deliberately decoupled. Each API
process uses a bounded pool (currently 32 connections) and returns connections
after short transactions. Remote queue claims use `FOR UPDATE SKIP LOCKED`, so
different workers can claim different rows concurrently; an advisory lock per
worker prevents its concurrent polls from exceeding `slots`. Executor lease
claims use an advisory lock per executor so capacity checks stay correct across
multiple scheduler replicas.

Do not raise every API replica's pool to the agent count. Budget total server
connections across all replicas below PostgreSQL `max_connections`; use
PgBouncer in transaction-pooling mode when replica count or burst traffic makes
that budget tight. Keep transactions free of model calls and filesystem work.

Monitor at least:

- pool acquisition timeout/error rate;
- `pg_stat_activity` active and waiting sessions;
- transaction latency and deadlocks/serialization retries;
- queued/claimed execution counts and oldest queued age;
- stale leases and requeue-limit failures.
