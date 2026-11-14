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

## Backup and restore

Routine backups, restore steps, drill cadence, and the vault master key
(`deploy/secrets/vault_master_key`) are covered in
[backup-restore-runbook.md](backup-restore-runbook.md). A database dump alone
is not a complete backup: the instance object store holds the material and
artifact bytes, and the vault ciphertext stored in PostgreSQL is useless
without the master key, which never lives in the database.

## Local setup

```bash
createdb agent_legion_dev
export AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion_dev
uv run uvicorn server.app.main:create_prod_app --factory --reload \
  --log-config deploy/uvicorn-log-config.json
```

Use a derived database name (as above), not the bare `agent_legion`:
`init_db` refuses to initialize/migrate the bare shared name without
`AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA=1` — that name is the code-default DSN
a misdirected process (worktree script without .env) would silently resolve,
so the refusal keeps such a process from migrating the shared database. The prod
launchers (`scripts/native-prod-up.sh`, `deploy/compose.host.yaml`) set the
opt-in themselves; a manual `uvicorn` against the shared database must set
it explicitly.

The server creates the current schema under a PostgreSQL advisory migration
lock keyed per (database, effective schema) — see `server/app/db/schema.py`.
The configured role needs permission to connect and to create/alter
objects in its application schema.

Schema upgrades are per-version: `schema_migrations` records one row per
registered version, and `init_db` only runs data migrations above
`max(version)` (the DDL file still replays in full on upgrade — it is
idempotent by construction). A database recorded at the current version is
a no-op, including legacy single-row installs. Once a process has verified
a database at the current version, `init_db` skips the locked transaction
entirely on later calls in that process (`db/schema_head_cache.py`); the
test harness invalidates that memo on schema rebuilds and disables it
inside `fresh_schema` tests.

## Seeding a derived dev instance from local prod

`scripts/seed_from_prod.py` reads the local prod Docker PostgreSQL container and
writes only the current worktree's derived resources. Run the read-only preview
first, then apply the same command without `--dry-run`:

```bash
uv run python scripts/seed_from_prod.py --dry-run
uv run python scripts/seed_from_prod.py
```

For the Docker prod stack, `deploy/.env` normally has the container-only S3
endpoint. Override it with the host loopback endpoint when needed (never print
or paste credential values into the command or logs):

```bash
SEED_PROD_S3_ENDPOINT=http://127.0.0.1:8333 \
  uv run python scripts/seed_from_prod.py --dry-run
```

The source is fixed to the prod database/bucket. The target database and bucket
must exactly match the current worktree's derived names (a tower child reuses
its parent's names), and database/S3 endpoints must be loopback. Every libpq
`host` / `hostaddr` candidate (including query overrides, comma-separated
fallbacks, and environment fallbacks) must be loopback. Any non-empty
`service` / `PGSERVICE` is rejected instead of reading a service file; use an
explicit loopback DSN. psql error output is
redacted because it can echo the DSN. The two S3 endpoints must identify the
same local instance because the copy uses S3
`CopyObject`; no bytes are downloaded and re-uploaded. A missing target bucket,
different bucket, remote endpoint, or endpoint mismatch aborts before Layer 1
writes. Credentials are read separately from prod `deploy/.env` and the dev
root `.env`; the target identity needs source read permission for
`CopyObject`.

The mapping and replacement policy are deliberately explicit:

- Layer 1 replaces the definition tables in full. Workspace ids are unchanged.
- Layer 2 selects the newest `--jobs-per-workflow` jobs per workspace and all
  `materials` rows. `job_artifacts` is restricted to those sampled jobs. Job,
  workspace, material, and artifact ids and every `storage_key` remain
  unchanged; only the bucket changes. Material `object_etag` is rebound from
  the destination HEAD after a copy.
- Existing target Layer 1/2 rows are replaced, not merged. Local job dirs and
  legacy CAS blobs retain their compatibility copy behavior and may be rerun;
  job dirs are only caches and `jobs.sqlite` is excluded. The legacy CAS is not
  a new artifact channel and its missing blobs do not create S3 manifest rows.
- Destination objects with the same key and size are HEAD-skipped. A same-key
  different-size object is a conflict and its manifest row is omitted unless
  `--overwrite-objects` is explicitly supplied. Missing sources, source size
  mismatches, HEAD/copy errors, and failed post-copy validation are reported
  per row and omitted from the load.
- All successful Layer 2 rows load in one transaction only after object
  preparation. A load failure rolls back the whole Layer 2 load; already copied
  objects remain and are HEAD-skipped on retry. Partial object failures leave a
  valid successful subset, produce exit code 1, and are safe to repair and
  rerun. Fatal guard/command errors use exit code 2. Dry-run performs source
  exports and HEAD reads but does not initialize a schema, write DB rows, copy
  files, or copy objects.

`--skip-files` suppresses only local job-dir/CAS copies. `--skip-objects`
explicitly selects the old behavior: neither `materials` / `job_artifacts` rows
nor their S3 objects are loaded. `--object-batch-size` bounds object-row
processing batches; source CSV export and target COPY remain streaming.

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
- Derived names normalize `_` / `-` / `.` and case (`foo_bar` and `foo-bar`
  share the same database/bucket); `scripts/init-worktree.sh` /
  `clean-worktree.sh` / `drop-worktree-db.sh` refuse names that collide with
  another worktree after normalization (#950) — avoid look-alike worktree
  names.
- A registered parent worktree may contain tower children at
  `.tower/worktrees/wt-*` (#1409). Their init accepts only this exact layout,
  copies the parent's `.env` and vault key, and seeds worker state without
  creating a child-specific database, bucket, or initialization marker. The
  parent must already have `.env` (including database and bucket settings) and
  `deploy/secrets/vault_master_key`; otherwise child init fails before making
  changes. Tower test databases remain independent and use
  `agent_legion_test_<parent>_<child>` so same-named children under different
  parents cannot share pytest schemas. Development-database/bucket collision
  exemptions do not skip this test-database check. `clean-worktree.sh` and
  `drop-worktree-db.sh` refuse to delete parent
  resources while registered tower children still exist; tear down those
  children through tower first.
- If `clean-worktree.sh` is SIGKILLed between removing the worktree and
  dropping its databases (or a worktree was removed by hand without cleanup),
  the stale derived databases survive. `init-worktree.sh` closes the
  silent-adoption window (#1349): while holding the derived-name lock, a
  fresh instance (no `.worktree-initialized.ok` marker, `.env` not yet
  pointing at its own derived database) that finds the derived databases
  already present refuses to start instead of silently reusing the previous
  instance's data. "Pointing at its own derived database" is judged by the
  exact anchored `AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/<db>`
  line init itself writes — deliberately not a substring match: a base
  worktree whose derived name is a superstring of this one (e.g. `victim2`
  vs `victim`) would otherwise make the just-copied `.env` read as
  "already mine" and reopen the window. The refusal also removes the `.env`
  copy it just made from the base worktree (it points at the base instance's
  database/bucket), so the refused instance is left with no marker and no
  `.env`, still counts as pending-initialization, and the delete guard does
  not block the remediation: drop the remnant with
  `scripts/drop-worktree-db.sh <name> --yes` and rerun init; or — after
  verifying the data really should be inherited — rerun init with
  `AGENT_LEGION_INIT_ADOPT_EXISTING_DB=1`. (A pre-existing `.env` is never
  removed.) Legacy instances created before the marker mechanism (no marker,
  but `.env` already carries their own derived database in that exact
  form) rerun fine, as do plain idempotent reruns (marker present). When
  `psql` is unavailable or PostgreSQL is unreachable, the existence probe
  cannot run; init then keeps its offline-tolerant behavior and only prints
  a notice that the check was skipped (no database action is possible in
  that state anyway).
- Day-to-day superuser connections (such as the OS-user role) can still
  drop anything — the guarantee covers the scripted routine path, not
  deliberate superuser operations.

## Capacity with hundreds of concurrent agents

Agent count and database connection count are deliberately decoupled. Each API
process uses a bounded pool (default 32 connections, overridable via the
`AGENT_LEGION_DB_POOL_MAX_SIZE` environment variable; see
`server/app/db/pool_sizing.py`) and returns connections after short
transactions. Connections are recycled explicitly: `AGENT_LEGION_DB_POOL_MAX_IDLE`
(default 120s) bounds how long an idle connection stays in the pool — note the
pool's idle shrink closes at most one connection per interval — and
`AGENT_LEGION_DB_POOL_MAX_LIFETIME` (default 900s) bounds total connection age.
A PostgreSQL backend's session memory (plan/sort caches) only returns to the OS
when its connection closes, so under sustained load these knobs keep long-lived
backends from ballooning; both default tighter than psycopg-pool's built-in
600s/3600s. Remote queue claims use `FOR UPDATE SKIP LOCKED`, so
different workers can claim different rows concurrently; an advisory lock per
worker (`agent-worker:<worker_id>`) prevents its concurrent polls from
exceeding its declared capacity. The local implicit
code pool uses a single advisory lock key (`pg_advisory_xact_lock(hashtext('code-pool'))`
in `server/app/executors/_lease_claims.py`) so capacity checks stay correct
across multiple scheduler replicas.

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
