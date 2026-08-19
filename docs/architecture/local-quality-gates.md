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
| Push (any branch) | Smoke (default): static checks + smoke test tier, lanes trimmed by pushed paths | `scripts/check-quick.sh` with `GATE_TIER=smoke` |
| Push with `AGENT_LEGION_GATE_LEVEL=quick` | Quick: full quick suite, lanes trimmed | `scripts/check-quick.sh` |
| Push with `AGENT_LEGION_GATE_LEVEL=full` | Full, locally | `scripts/check.sh` |
| PR to `develop`/`main`/`master`, push to `main`/`master` | Full | CI jobs `backend-unit` + `backend-postgres-a/b/c` + `frontend-*` + `rust` + `e2e-smoke` |
| Weekly schedule, manual dispatch | Extended | CI jobs `ci-extended` + `nightly-e2e` |

The pre-push hook diffs the pushed commits against their remote base and runs
only the affected quick-gate lanes locally: frontend-only changes skip the
backend pytest lane, docs-only changes run static checks only, and
backend-only changes skip Vitest. New branches/tags, shared files
(`pyproject.toml`, `uv.lock`, `scripts/`, `.github/`, `config/`, …), mixed
diffs, and any diff failure fall back to all lanes. CI always runs every lane
of the full quick suite, so trimming never weakens the server-side boundary.
The lane set and the test tier are part of the local evidence fingerprint, so
evidence from a trimmed run is never reused for a different lane set or tier.

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
runs in the `backend-postgres-a/b/c` jobs described below.

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
save Actions minutes), a weekly schedule, plus manual dispatch. Docs-only
changes (`docs/**`, `**/*.md`, `LICENSE`) do not trigger the workflow at all
(`paths-ignore`). Schedule runs skip every regular lane in the `changes` job
— the full gate already ran on the push/PR that produced the code — so only
`ci-extended` and `nightly-e2e` run weekly:

- **backend-unit** — static checks (ruff, format, mypy, architecture contracts,
  invariant registry, spec health) plus the PostgreSQL-offline unit tier
  (`GATE_TIER=unit`), uploading its coverage data file as a 1-day artifact.
- **backend-postgres-a** — the api:check OpenAPI contract step (Python +
  Postgres + node_modules) and postgres tier shard 1/3, then downloads every
  shard's coverage artifact, merges them with `coverage combine`, and
  enforces the 85% floor once on the combined report.
- **backend-postgres-b** — postgres tier shard 2/3 plus the
  `tests/full -m full_gate` evidence, uploading its coverage data file.
- **backend-postgres-c** — postgres tier shard 3/3, uploading its coverage
  data file.
- **frontend-logic / frontend-component / frontend-coverage** — frontend
  static checks and the two Vitest projects (node / jsdom) as parallel jobs;
  the coverage job merges the shard blob reports and enforces the frontend
  coverage thresholds plus the production bundle (`npm run build:bundle`).
- **rust** — `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`,
  and `cargo test` in `velites/`.
- **e2e-smoke** — the deterministic browser smoke suite.
- **ci-extended** — `tests/ci -m ci_extended` stress scenarios. Runs only on
  the weekly schedule and manual dispatch; PR and push runs skip it.
- **nightly-e2e** — multi-browser smoke E2E (the deterministic browser suite
  re-run on Chromium, Firefox, and WebKit via `scripts/e2e/run_browser_smoke.py`;
  PR/push stays Chromium-only) plus a workspace stress run
  (`scripts/stress/run_e2e_stress.py`, 50 agents / 2000 jobs / 300s at 200
  events/s, asserting p95 click latency and uploading the stress report).
  Runs only on the weekly schedule and manual dispatch.

The postgres tier shards are a deterministic `md5(nodeid) % 3` collection
filter (`scripts/pytest_gate_shard.py`, `GATE_SHARD=i/n`). Every pytest shard
writes its own `COVERAGE_FILE` with `--cov-fail-under=0`, so only the
combined report in backend-postgres-a enforces the 85% floor.

CI environment notes:

- Each job gets a fresh `postgres:17` service container; `AGENT_LEGION_DATABASE_URL`
  and `AGENT_LEGION_TEST_DATABASE_URL` point at it. The test database and worker
  schemas are created lazily when the PostgreSQL layer starts; importing the
  test support module and running the unit layer never connects to PostgreSQL.
- The api:check contract step regenerates frontend API types through
  `create_app` + node_modules, so it lives in the backend-postgres-a job; the
  frontend jobs need neither Python nor Postgres.
- `AGENT_LEGION_SKIP_SKILLS_SHARED_CHECK=1` skips `check-skills-shared.py` in CI:
  the built-in skill sources (`server/app/skills/builtin_sources.py`) point at
  machine-local skill repos (`~/.agents/skills/...`) that do not exist on
  runners. Local gates still run the check.
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
2. Require the `backend-unit`, `backend-postgres-a`, `backend-postgres-b`,
   `backend-postgres-c`, `frontend-logic`, `frontend-component`,
   `frontend-coverage`, `rust`, and `e2e-smoke` status checks to pass before
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
