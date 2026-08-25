# PostgreSQL runbook

PostgreSQL is the only runtime database. The API, scheduler replicas, sweepers,
and remote workers coordinate through the same database; there is no SQLite
fallback.

## Versions

The pinned version is **PostgreSQL 17** everywhere: the Docker stacks run
`postgres:17.5-bookworm` (`deploy/compose.host.yaml`) and CI test jobs run
`postgres:17`. For local development, install the matching major via Homebrew
(`brew install postgresql@17`) so client tools and server stay on 17.

Upgrading an existing deployment from an older major requires a dump/restore
(`pg_dump` from the old cluster, restore into a fresh 17 cluster) — rehearse it
before the maintenance window, and never point the stack at an old-major data
volume in place (the on-disk format is not compatible across majors).

## Local setup

```bash
createdb agent_legion
export AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion
uv run uvicorn server.app.main:create_prod_app --factory --reload
```

The server creates the current schema under a PostgreSQL advisory migration
lock. The configured role needs permission to connect and to create/alter
objects in its application schema.

## Dev-machine role isolation and worktree DB cleanup

`DROP DATABASE` requires the database owner or a superuser. To make the
routine cleanup path physically unable to drop the shared/prod database,
dev machines should use two dedicated roles:

```sql
CREATE ROLE agent_legion_prod NOLOGIN;
CREATE ROLE agent_legion_dev LOGIN CREATEDB;
ALTER DATABASE agent_legion OWNER TO agent_legion_prod;
-- and for every derived worktree database:
ALTER DATABASE <agent_legion_worktree> OWNER TO agent_legion_dev;
```

- `agent_legion` (the code-default shared/prod database) is owned by the
  NOLOGIN `agent_legion_prod` role; derived per-worktree databases
  (`agent_legion_<worktree>` / `agent_legion_test_<worktree>`) belong to the
  non-superuser `agent_legion_dev` role. `scripts/init-worktree.sh` and the
  test bootstrap (`tests/postgres_support.py`) align ownership on creation
  when the role exists.
- Always remove a retired worktree's databases through
  `scripts/drop-worktree-db.sh <worktree-name>`, never a bare `dropdb`. The
  script derives the two database names from the worktree name (the bare
  shared/prod name is unreachable by construction), connects as
  `agent_legion_dev` when the role exists (PostgreSQL then rejects any drop
  outside the derived set), and prints owner/size before asking for
  confirmation.
- Day-to-day superuser connections (such as the OS-user role) can still
  drop anything — the guarantee covers the scripted routine path, not
  deliberate superuser operations.

## Capacity for 200–300 agents

Agent count and database connection count are deliberately decoupled. Each API
process uses a bounded pool (currently 32 connections, overridable via the
`AGENT_LEGION_DB_POOL_MAX_SIZE` environment variable; see
`server/app/db/pools.py:16-17`) and returns connections after short
transactions. Connections are recycled explicitly: `AGENT_LEGION_DB_POOL_MAX_IDLE`
(default 120s) bounds how long an idle connection stays in the pool — note the
pool's idle shrink closes at most one connection per interval — and
`AGENT_LEGION_DB_POOL_MAX_LIFETIME` (default 900s) bounds total connection age.
A PostgreSQL backend's session memory (plan/sort caches) only returns to the OS
when its connection closes, so under sustained load these knobs keep long-lived
backends from ballooning; both default tighter than psycopg-pool's built-in
600s/3600s. Remote queue claims use `FOR UPDATE SKIP LOCKED`, so
different workers can claim different rows concurrently; an advisory lock per
worker prevents its concurrent polls from exceeding `slots`. The local implicit
code pool uses a single advisory lock key (`pg_advisory_xact_lock(hashtext('code-pool'))`
in `server/app/executors/_lease_claims.py`) so capacity checks stay correct
across multiple scheduler replicas; per-executor locks retired with executor
definitions at schema v47 (P-0.5).

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
