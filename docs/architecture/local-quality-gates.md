# Quality Gates: Local Hooks + GitHub Actions CI

## Purpose

Slow quality gates run on GitHub Actions hosted runners
(`.github/workflows/quality-gate.yml`); local hooks keep only the fast
feedback loop on the maintainer machine. Branch protection on GitHub (required
status checks) is the server-side trust boundary that local hooks cannot
provide.

## Gate Levels

| Event | Gate | Command / CI job |
| --- | --- | --- |
| Commit | Fast | `scripts/check-fast.sh` |
| Edit-test iteration (agent inner loop) | Affected: backend affected-test selection over the unit tier + frontend `vitest related` | `GATE_TIER=aff ./scripts/check-quick.sh` |
| Push (any branch) | Smoke (default): static checks + smoke test tier, lanes trimmed by pushed paths | `scripts/check-quick.sh` with `GATE_TIER=smoke` |
| Push with `AGENT_LEGION_GATE_LEVEL=quick` | Quick: unit-tier quick suite, lanes trimmed | `scripts/check-quick.sh` |
| Push with `AGENT_LEGION_GATE_LEVEL=full` | Full, locally | `scripts/check.sh` |
| PR to `develop`/`main`/`master`, push to `main`/`master` | Full | CI jobs `backend-unit` + `api-check` + `backend-postgres-a/b/c` + `backend-coverage` + `frontend-*` + `rust` + `e2e-smoke` |
| Weekly schedule, manual dispatch | Extended | CI jobs `ci-extended` + `nightly-e2e` (`nightly-gate.yml`) |

The pre-push hook diffs the pushed commits against their remote base and runs
only the affected quick-gate lanes locally: frontend-only changes skip the
backend pytest lane, docs-only changes run static checks only, and
backend-only changes skip Vitest. New branches/tags, shared files
(`pyproject.toml`, `uv.lock`, `scripts/`, `.github/`, `config/`, …), mixed
diffs, and any diff failure fall back to all lanes. CI always runs every lane
of the full quick suite, so trimming never weakens the server-side boundary.
The lane set and the test tier are part of the local evidence fingerprint, so
evidence from a trimmed run is never reused for a different lane set or tier.

Per-lane parallelism defaults are worktree-aware
(`detect_gate_default_jobs_worktree_aware` in `scripts/gate-jobs.sh`): the
machine budget is divided across the gates actually running — N concurrent
gates each get `(cores-2)/N` workers, clamped to `[2, 8]` (with the default
serialized queue, N is 1 and a gate gets the full `cores-2` budget). When
the machine-wide queue is not visible (stubbed git in fixture repos), the
fallback probes sibling `.quick-gate.lock` directories through
`git worktree list`: `min(4, cores)` while a sibling worktree runs a gate,
`cores-2` otherwise. Per-lane env overrides
(`AGENT_LEGION_TEST_WORKERS`, `AGENT_LEGION_FRONTEND_TEST_WORKERS`,
`AGENT_LEGION_RUST_WORKERS`) still win.

## Machine-Wide Gate Queue

Several agent worktrees on one host can fire quality gates simultaneously;
uncoordinated, they oversubscribe the CPU (observed: the last of four
concurrent quick gates stretched to ~1h while a lone gate takes ~6min, and
even two concurrent gates made timing-sensitive tests flake on timeouts —
each gate fans out into parallel lanes, so the machine saw ~2x its core
count in jobs). `scripts/check-quick.sh` therefore acquires a machine-wide
gate slot (`scripts/gate-queue.sh`) before running lanes:

- Slots live in `<git-common-dir>/gate-slots/` — the one path every worktree
  of the repository shares on a host — each recording pid, worktree, and
  start time.
- At most `AGENT_LEGION_MAX_PARALLEL_GATES` gates run concurrently (default
  **1** — gates serialize and each runs at the full machine budget; `2` is
  opt-in for big boxes, `0` disables the queue). A gate finding all slots
  taken waits, printing the current holders on entry and every 30s.
- Stale slots are reclaimed on sight: a slot whose pid is dead, or older than
  `AGENT_LEGION_GATE_SLOT_MAX_AGE_SECONDS` (default 7200 — bounds the
  zombie-pid hole where a wrapper forgets to reap its exited child), is
  removed by the next acquirer.
- The slot is taken after the per-worktree `.quick-gate.lock`, so
  same-worktree serialization stays first; `check.sh` calls `check-quick.sh`
  sequentially and each invocation takes and releases its own slot.
- Waiting is correct behavior, not a failure: agents should do non-CPU work
  (reading, writing code) while queued, and never bypass the queue. A queued
  gate costs its full runtime, not more — serialization removed the
  contention that stretched concurrent gates and flaked their tests, so the
  queue now moves at lone-gate speed end to end.

Backend pytest lanes distribute xdist work with `--dist worksteal`: the
default `load` scheduler hands each worker a batch of tests up front, so one
slow test strands its whole batch and the suite waits on a single busy
worker. worksteal lets idle workers steal pending tests, shrinking that
tail (with `--reruns 1` kept as insurance for genuinely timing-sensitive
tests).

Within a gate, the test round is staggered: the backend lane runs alone
first, then frontend and rust run in parallel. Starting all three test
lanes together oversubscribed the machine from the inside (~20 jobs on a
10-core box) — the same CPU contention the machine-wide queue removed
between gates. Measured on an idle machine: the backend unit tier alone
takes ~44s, yet stretched past 10 minutes inside a fully parallel gate.
The static round stays fully parallel (lint/typecheck are light), and the
`test` round inside `check-quick-frontend.sh`/`run_rust_round` lanes is
unaffected when invoked standalone.

The smoke tier (`GATE_TIER=smoke`) replaces the backend pytest lane with a
curated subset — every architecture governance test plus one core behavioral
file per subsystem, assigned by path in `tests/conftest.py`
(`_SMOKE_TEST_FILES`) and selected with `-m "smoke"`.
It runs without coverage because the 85% floor only applies to the full
suite. Keep the tier under ~90 seconds: when adding tests for a new
subsystem, add one core file to the smoke set rather than raising the budget.

The unit tier (`GATE_TIER=unit`) runs the complete PostgreSQL-offline unit
layer, selected with `-m "not postgres and not repository_gate"` against an
unreachable loopback database URL, so an accidental database dependency fails
the gate instead of silently using a developer database. CI runs it as the
`backend-unit` job; the PostgreSQL integration layer (`GATE_TIER=postgres`)
runs in the `backend-postgres` matrix job (checks `backend-postgres-a/b/c`)
described below.

The full tier (`GATE_TIER=full`, the default for `check-quick.sh` without a
tier override) selects the same unit layer as `GATE_TIER=unit`: the
PostgreSQL integration layer (~47% of the quick suite's tests and ~2.5x the
unit tier's wall time) moved out of the local default because CI re-runs all
of it on every PR — paying it on every local gate bought little. Before
handing off database-touching work, run `GATE_TIER=postgres
./scripts/check-quick-backend.sh` explicitly (or rely on CI).
`scripts/check.sh` — the local full-gate substitute — still pins both tiers
itself (unit segment, then postgres appended onto the same coverage file),
so its combined coverage report keeps seeing the whole suite. The postgres
segment re-enters the quick gate for its test round only
(`GATE_SKIP_STATIC=1` skips the static round and the api-contract step;
`BACKEND_SKIP_WORKER_UI_TESTS=1` skips the tier-independent worker UI
tests): the unit segment already ran those, so every check still runs
exactly once per full gate, and the worktree lock, machine slot, and
coverage append semantics are unchanged.

The affected tier (`GATE_TIER=aff`) is the edit-test iteration loop for
agents and humans alike: the backend lane selects tests whose recorded
coverage intersects the changed source files (index in
`.pytest-aff-index.json`, distilled from a one-off `GATE_TIER=aff-index`
run with `--cov-context=test`), and the frontend lane runs `vitest related`
over the changed frontend files. It falls back to the plain unit tier when
no index exists, when a changed source file is missing from the index (an
index blind spot — the affected tests are unknown), when the selection
would run most of the suite anyway, or when the changed set includes shared
files — the fallback never widens what runs. Deleted test files are dropped
from the selection (a stale path would fail pytest collection). An aff pass
is **not** gate evidence: `scripts/run-local-gate.sh` rejects the tier, and
the full suite remains the pre-push/CI boundary. Rebuild the index after
dependency or conftest changes (a stale index only slows the loop —
unmapped sources force the fallback, and unmapped test files still run
wholesale via the tests/ rule in `scripts/pytest_aff_selection.py`).

Install the repository-managed hooks once from a worktree that contains `.githooks/`:

```bash
make install-hooks
```

The installer copies small dispatchers into the Git common hooks directory. A dispatcher resolves
the current worktree root and executes its versioned `.githooks/` implementation. If an older
branch does not contain `.githooks/`, the dispatcher exits successfully and leaves that worktree
unaffected. Passing evidence is shared through the same Git common directory.

## CI Workflow

`.github/workflows/quality-gate.yml` runs on pull requests to
`develop` / `main` / `master`, pushes to `main` / `master` (a `develop`
merge is already covered by its PR gate, so push runs there were dropped to
save Actions minutes), plus manual dispatch. Docs-only changes (`docs/**`,
`**/*.md`, `LICENSE`) do not trigger the workflow at all (`paths-ignore`).
The weekly schedule lives in `.github/workflows/nightly-gate.yml` (issue
#193), which runs only `ci-extended` and `nightly-e2e` — the stress jobs
never run on PR/push, and a scheduled trunk run in the quality-gate file
shared its concurrency group, so it could cancel an in-flight push gate for
the same ref:

- **backend-unit** — static checks (ruff, format, mypy, architecture contracts,
  invariant registry, spec health) plus the PostgreSQL-offline unit tier
  (`GATE_TIER=unit`), uploading its coverage data file as a 1-day artifact.
- **api-check** — the api:check OpenAPI contract step (Python + Postgres +
  node_modules) and the worker UI node:test suite. Its own lightweight job
  (issue #193): it runs on the frontend lane too, so frontend-only PRs no
  longer drag a postgres-test job along just for the contract check.
- **backend-postgres-a/b/c** — the postgres tier's three hash shards as one
  matrix job (`backend-postgres`, issue #193): each leg runs its
  `GATE_SHARD=i/n` tier slice and uploads coverage data + telemetry as 1-day
  artifacts. Shard b additionally runs the velites sandbox integration check
  and the `tests/full -m full_gate` evidence layer. `fail-fast` is off, so a
  failing shard does not cancel its peers' evidence.
- **backend-coverage** — downloads every shard's coverage artifact, merges
  them with `coverage combine`, and enforces the 85% floor once on the
  combined report. The needs-DAG (`backend-unit` + `backend-postgres`)
  guarantees every producer finished before the merge starts — the
  event-driven replacement for the old `gh api` artifact polling in
  backend-postgres-a (issue #193). It also renders the aggregate backend
  test summary.
- **frontend-logic / frontend-component / frontend-coverage** — frontend
  static checks and the two Vitest projects (node / jsdom) as parallel jobs;
  the coverage job merges the shard blob reports and enforces the frontend
  coverage thresholds plus the production bundle (`npm run build:bundle`).
- **rust** — `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`,
  and `cargo test` in `velites/`.
- **e2e-smoke** — the deterministic browser smoke suite.
- **docker-build** — CI-only image build lane (host + worker targets). It runs
  only when the `changes` job detects image-relevant path changes
  (`Dockerfile`, `.dockerignore`, dependency locks, `worker/`, `shared/`,
  `deploy/`); no other job exercises the Dockerfile.

In `nightly-gate.yml`:

- **ci-extended** — `tests/ci -m ci_extended` stress scenarios, with the
  unregistered-rerun (flaky governance) check. Runs only on the weekly
  schedule and manual dispatch.
- **nightly-e2e** — multi-browser smoke E2E (the deterministic browser suite
  re-run on Chromium, Firefox, and WebKit via `scripts/e2e/run_browser_smoke.py`;
  PR/push stays Chromium-only) plus a workspace stress run
  (`scripts/stress/run_e2e_stress.py`, 50 agents / 2000 jobs / 300s at 200
  events/s, asserting p95 click latency and uploading the stress report).
  Runs only on the weekly schedule and manual dispatch.

The postgres tier shards are a deterministic `md5(nodeid) % 3` collection
filter (`scripts/pytest_gate_shard.py`, `GATE_SHARD=i/n`). Every pytest shard
writes its own `COVERAGE_FILE` with `--cov-fail-under=0`, so only the
combined report in backend-coverage enforces the 85% floor.

CI environment notes:

- Each job gets a fresh `postgres:17` service container; `AGENT_LEGION_DATABASE_URL`
  and `AGENT_LEGION_TEST_DATABASE_URL` point at it. The test database and worker
  schemas are created lazily when the PostgreSQL layer starts; importing the
  test support module and running the unit layer never connects to PostgreSQL.
- The api:check contract step regenerates frontend API types through
  `create_app` + node_modules, so it lives in the api-check job; the
  frontend test jobs need neither Python nor Postgres.
- uv and npm caches are enabled; the first cold run is dominated by dependency
  downloads and takes substantially longer than cached runs.

## Test Telemetry

CI test lanes emit lightweight, aggregate telemetry without retaining raw
failure or source context as downloadable artifacts:

- each backend unit, PostgreSQL, and full pytest layer prints its 30 slowest
  tests, writes ephemeral JUnit XML, and records pytest-rerunfailures attempts
  through `scripts.pytest_telemetry`;
- frontend Vitest writes ephemeral JUnit and JSON reports alongside its normal
  console and coverage reporters;
- `scripts/summarize_test_results.py` adds aggregate counts, case time, rerun
  counts, commit, platform, CPU, and tool versions to the GitHub job summary;
- raw JUnit, Vitest JSON, and HTML coverage remain on the temporary runner and
  are not uploaded, because they can contain private test names, failure data,
  or source context.

The gate scripts only enable file reporters when
`AGENT_LEGION_TEST_RESULTS_DIR` is set, so ordinary local runs keep their
existing output and overhead. `AGENT_LEGION_TEST_DURATIONS` controls the pytest
slow-test count and defaults to 30 in telemetry mode.

## Exact-Commit Evidence (Local)

Before running a pre-push gate, `scripts/run-local-gate.sh` requires a clean worktree. A successful
result is stored under:

```text
<git-common-dir>/local-gates/<commit-sha>/<gate>-<fingerprint>.pass
```

The fingerprint includes the gate scripts, dependency lock files, architecture registries, and
local tool versions. Repeated pushes of the same unchanged commit reuse the evidence. Set
`AGENT_LEGION_LOCAL_GATE_FORCE=1` to run the gate again.

The evidence is intentionally local and is never committed. Server-side
verification comes from the CI workflow, not from these files.

## Required GitHub Settings

Configure the repository on GitHub as follows:

1. Protect `develop` and any release branches (Settings → Branches, or Rules → Rulesets).
2. Require the `backend-unit`, `api-check`, `backend-postgres-a`,
   `backend-postgres-b`, `backend-postgres-c`, `backend-coverage`,
   `frontend-logic`, `frontend-component`,
   `frontend-coverage`, `rust`, `e2e-smoke`, and `docker-build` status checks
   to pass before
   merging; require branches to be up to date.
3. Disable force-push and branch deletion for protected branches.
4. Merge changes through a pull request; do not edit protected branches in the web UI.

Until required status checks are configured, nothing server-side blocks a red
merge — the protection is only as strong as this one-time setup.

## Extended Gate Policy

The `ci-extended` CI job covers the areas that previously required a manual
`scripts/check-ci.sh` run:

- PostgreSQL schema migration, offline SQLite import, backup, or restore;
- executor leases, capacity, cancellation, or worker concurrency;
- filesystem deletion, path validation, or artifact recovery;
- release tags or a large multi-branch integration.

The job runs on the weekly schedule, so no manual step is needed for routine
work; use `workflow_dispatch` to run it against any ref before a risky merge
(schema migrations, executor concurrency, filesystem deletion, release
tags). If a deterministic test cannot run in the CI environment, record the
exact failure in the pull request and rerun where the required resource is
available. Do not record passing evidence for a partial gate.

## Quality Impact

- Fast feedback remains cheap enough to run on every commit; pushes default
  to the curated smoke tier, while the complete unit and PostgreSQL layers
  run as parallel CI jobs on every PR/push.
- CI adds the PostgreSQL and full layers on every PR/push, so database and
  cross-control-plane regressions are still caught server-side before merge.
- Stress evidence runs weekly instead of on every push, trading same-day
  detection for a much cheaper push loop; risky changes can trigger it on
  demand via `workflow_dispatch`.
- Hooks can still be bypassed with `--no-verify`; the required status checks
  on GitHub are the actual merge boundary.
- CI runs in a clean environment (fresh Postgres, no local skill repos, no
  `.env`), which also proves the gates are environment-independent.
