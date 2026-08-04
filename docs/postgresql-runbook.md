# PostgreSQL runbook

PostgreSQL is the only runtime database. The API, scheduler replicas, sweepers,
and remote workers coordinate through the same database; there is no SQLite
fallback.

## Local setup

```bash
createdb agent_legion
export AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion
uv run uvicorn server.app.main:app --reload
```

The server creates the current schema under a PostgreSQL advisory migration
lock. The configured role needs permission to connect and to create/alter
objects in its application schema.

## Capacity for 200–300 agents

Agent count and database connection count are deliberately decoupled. Each API
process uses a bounded pool (currently 32 connections, overridable via the
`AGENT_LEGION_DB_POOL_MAX_SIZE` environment variable; see
`server/app/db/pools.py:16-17`) and returns connections after short
transactions. Remote queue claims use `FOR UPDATE SKIP LOCKED`, so
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
