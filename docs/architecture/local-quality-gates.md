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
| Push (any branch) | Smoke (default): static checks + smoke test tier, lanes trimmed by pushed paths | `.githooks/pre-push` → `scripts/run-local-gate.sh` → `scripts/check-quick.sh` with `GATE_TIER=smoke` |
| Push with `AGENT_LEGION_GATE_LEVEL=quick` | Quick: unit-tier quick suite, lanes trimmed | same hook path → `scripts/check-quick.sh` |
| Push with `AGENT_LEGION_GATE_LEVEL=full` | Full, locally | same hook path → `scripts/check.sh` |
| PR to `main`/`master`/`release/*`, push to `main`/`master` | Full | CI lanes run in parallel; stable aggregate check `quality-gate` is the merge boundary |
| Weekly schedule, manual dispatch | Extended | CI jobs `ci-extended` + `nightly-e2e` + `exemption-expiry` + `deps-audit` (`nightly-gate.yml`) |

The pre-push hook diffs the pushed commits against their remote base and runs
only the affected quick-gate lanes locally: frontend-only changes skip the
backend pytest lane, docs-only changes run static checks only, and
backend-only changes skip Vitest. New branches/tags, shared files
(`pyproject.toml`, `uv.lock`, `scripts/`, `.github/`, `config/`, …), mixed
diffs, and any diff failure fall back to all lanes. Only `docs/**`,
repository-root `*.md` and `LICENSE` count as docs: nested markdown such as
the Studio bootstrap prompt, MCP guides or example `SKILL.md` files is a
runtime input and runs its directory's lane, and `velites/schema/**` also runs
the backend lane (Python contract tests read it). The rule lives in
`scripts/lane-paths.sh`, sourced by the CI `changes` job, `check-quick.sh` and
the pre-push hook, and `tests/scripts/test_lane_paths.py` pins all three to
one path table (#941). CI always runs every lane
of the full quick suite, so trimming never weakens the server-side boundary.
The lane set and the test tier are part of the local evidence fingerprint, so
evidence from a trimmed run is never reused for a different lane set or tier.
Local checks are feedback rather than the trust boundary: parallel worktrees
use the affected tier while editing and the path-trimmed smoke hook on push;
they do not each repeat the complete unit or PostgreSQL suite before CI.

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
file per subsystem, listed in `config/architecture/smoke-test-files.json`
(loaded by `tests/conftest.py`) and selected with `-m "smoke"`.
It runs without coverage because the 85% floor only applies to the full
suite. Keep the tier under ~90 seconds: when adding tests for a new
subsystem, add one core file to the smoke set rather than raising the budget.

The unit tier (`GATE_TIER=unit`) runs the complete PostgreSQL-offline unit
layer, selected with `-m "not postgres"` against an
unreachable loopback database URL, so an accidental database dependency fails
the gate instead of silently using a developer database. CI runs it as the
`backend-unit` job; the PostgreSQL integration layer (`GATE_TIER=postgres`)
runs in the `backend-postgres` matrix job (checks `backend-postgres-a/b/c`)
described below.

The full tier (`GATE_TIER=full`, the default for `check-quick.sh` without a
tier override) selects the same unit layer as `GATE_TIER=unit`: the
PostgreSQL integration layer (~47% of the quick suite's tests and ~2.5x the
unit tier's wall time) moved out of the local default because CI re-runs all
of it on every PR — paying it on every local gate bought little. Database
development runs directly related postgres tests in the inner loop and relies
on the PR shards for the complete tier.
`scripts/check.sh` — the local full-gate substitute — still pins both tiers
itself (unit segment, then postgres appended onto the same coverage file),
so its combined coverage report keeps seeing the whole suite. The postgres
segment re-enters the quick gate for its test round only
(`GATE_SKIP_STATIC=1` skips the static round and the api-contract step;
`BACKEND_SKIP_WORKER_UI_TESTS=1` skips the tier-independent worker UI
tests): the unit segment already ran those, so every check still runs
exactly once per full gate, and the worktree lock, machine slot, and
coverage append semantics are unchanged. Use this local full substitute only
when CI is unavailable or an offline release credential is required.

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
`main` / `master` / `release/*`, merge-queue synthetic commits,
pushes to `main` / `master`, plus manual dispatch. Docs-only changes (`docs/**`,
repository-root `*.md`, `LICENSE`) still trigger the workflow but every backend/frontend
lane evaluates to false in the `changes` job and skips without acquiring a
runner, including the complete `backend-postgres` matrix. The `docs-terms`
guard is the one check that still runs on
that path (codex review on #375/#377): docs-only PRs are exactly the ones
that can reintroduce retired terminology into current-state docs, so the
retired-terms check gets its own lightweight job that executes whenever the
backend lane is off (`backend-unit` runs it inside `check_architecture`
whenever the backend lane is on — the two entries are exact complements).
A `paths-ignore` trigger would keep the workflow from
starting at all and leave required checks pending forever — the docs-only
PR deadlock first hit on #316 (single jobs) and #319 (matrix shards).
Workflow and composite-action changes force all four path flags on, so an
edited Rust or Docker lane cannot skip its own validation. The weekly schedule
lives in `.github/workflows/nightly-gate.yml` (issue #193) and runs the four
jobs listed below (`ci-extended`, `deps-audit`, `exemption-expiry`,
`nightly-e2e`); none of them runs on PR/push, and keeping the schedule out of
the quality-gate file keeps it out of that file's per-ref concurrency group. Branch protection requires only the final `quality-gate` job.
It runs with `always()`, reads every lane result and the path-selection flags
through `needs`, requires every selected lane to succeed, accepts skips only
for unselected lanes, and rejects failures or cancellations. Internal job
names and shard counts can therefore change without rewriting protected-branch
contexts:

Merge-group static checks retain the normal `HEAD` / `HEAD^` monotonicity
anchors. The queue rebuilds synthetic commits against the latest base and any
preceding queued PRs, so an individual PR's earlier green result cannot opt the
combined commit out of the newer budget and data-boundary floors. Only an
explicit same-repository release-train PR and the post-merge trunk push use the
release-train `HEAD`-only exception.

- **backend-unit** — static checks (ruff, format, mypy, architecture contracts,
  invariant registry, spec health, version-manifest consistency via
  `scripts/check_versions.py` — the decoupled versioning discipline for
  velites/frontend, see CONTRIBUTING "House rules") plus the
  PostgreSQL-offline unit tier (`GATE_TIER=unit`), uploading its coverage
  data file as a 1-day artifact.
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
  test summary, and runs the coverage-partition check in `--enforce` mode
  for the backend partitions (agent dispatch / workflow upgrade / agent
  artifacts / worker execution plane / job log raw, #275+#295). Local
  `check.sh` keeps partitions report-only — its coverage file may hold a
  partial tier, and floors on partial data produce false reds. It also runs
  `check_reruns.py` against every shard report: a retry-pass is merge-blocking
  unless its exact nodeid has a registry entry. An expired deadline is only
  listed here, never fails the PR (#941: a calendar date must not red
  unrelated PRs); the nightly `exemption-expiry` job enforces it. Every PR
  (any target branch; scheduled jobs only see the default branch and only
  after the merge) additionally passes the target branch's registry
  (`--base-registry`) and fails on entries the PR itself adds or re-targets
  (deadline, nodeid, scope, recurring, registered_on / extended_on — #1034)
  with an already expired deadline.
- **frontend-logic / frontend-component-a/b / frontend-coverage** — frontend
  static checks and the two Vitest projects (node / jsdom) as parallel jobs;
  the slower component project is split again with Vitest's deterministic
  native `--shard=1/2` partition;
  the coverage job merges the shard blob reports and enforces the frontend
  coverage thresholds plus the production bundle (`npm run build:bundle`),
  and enforces the frontend coverage partitions (auth/bootstrap / api
  transport / workflow upgrade / admin pages, #295) against the merged blob
  data — previously every frontend partition reported SKIP on PR runs
  because only the backend-coverage job ran the partition check.
- **rust** — `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`,
  and `cargo test` in `velites/`.
- **e2e-smoke** — the deterministic browser smoke suite.
- **docs-terms** — the retired-terms docs guard as a standalone lightweight
  job (`uv run --no-project --with pyyaml`, no uv sync). It runs when the
  backend lane is off and skips otherwise — the exact complement of the
  `check_architecture` static round inside `backend-unit`.
- **docker-build** — CI-only image build lane (host + worker targets). It runs
  only when the `changes` job detects image-relevant path changes
  (`Dockerfile`, `.dockerignore`, dependency locks, `worker/`, `shared/`,
  `deploy/`); no other job exercises the Dockerfile.
- **quality-gate** — stable final context required by branch protection;
  succeeds only when every selected lane succeeded (intentional path skips are
  neutral).

In `nightly-gate.yml`:

- **ci-extended** — `tests/ci -m ci_extended` stress scenarios, with the
  unregistered-rerun (flaky governance) check. Runs only on the weekly
  schedule and manual dispatch.
- **deps-audit** — dependency vulnerability audit via `make audit`
  (`scripts/check-deps-audit.sh`: pip-audit over the frozen `uv.lock` export
  plus `npm audit --omit=dev --audit-level=high`; #969). It queries live
  advisory databases, so its verdict changes over time on an unchanged tree —
  that is why it runs on the weekly schedule and manual dispatch instead of
  the PR gate. Any finding fails the job; fixes land as dependency PRs.
- **exemption-expiry** — refreshes the issue-state manifest and detects
  expired architecture exemptions; the refreshed manifest is committed to a
  dedicated `chore/issue-states-sync` branch and merged via an auto-merge PR
  by the job itself (#1150: the manual
  `make architecture-issue-states` ritual rots on a single-maintainer repo —
  five weeks of red nightly proved it), while a closed anchor issue still
  fails the job so the exemption gets fulfilled or re-anchored first — the
  docs-only lane (changes + docs-terms) gates the merge, so the bot never
  bypasses the trunk ruleset. Since #295 it also detects expired
  flaky-registry deadlines (`check_reruns.py --check-deadlines`, deadline
  evidence without needing the extended rerun report), and since #1024 on
  every maintained branch too (`scripts/quality/flaky_branch_deadlines.py`:
  each `release/X.Y.Z` above the default branch's version,
  registries read leniently from the fetched branch tips). It is the only lane
  that fails on an expired deadline (#941) and annotates entries due within
  7 days as warnings; PR backend-coverage enforces observed reruns only. The
  registry's clock-free rules (one entry per nodeid, deadline at most 45
  days after the entry's `registered_on` / `extended_on`) are enforced on
  every load, including the unit-tier `tests/scripts/test_flaky_registry_entries.py`.
- **nightly-e2e** — multi-browser smoke E2E (the deterministic browser suite
  re-run on Chromium, Firefox, and WebKit via `scripts/e2e/run_browser_smoke.py`;
  PR/push stays Chromium-only) plus a workspace stress run
  (`scripts/stress/run_e2e_stress.py`, 50 agents / 2000 jobs / 300s at
  200 events/s, asserting p95 click latency and uploading the stress report).
  Runs only on the weekly schedule and manual dispatch.

### Timing-assertion discipline (#1150)

The loaded-runner flaky family (10 of 16 registry entries at the time of
writing) is not test bugs: it is timing-sensitive assertions meeting a 2-core
CI runner. Every new or touched timing-adjacent test follows four rules —
review checks them like a boundary rule:

1. **Wait for signals, not durations.** Assert after an observable state
   exists (`tests/helpers.wait_for_predicate`; never a fixed sleep, never an
   assumption that a poll window of N seconds is enough). Local `_wait_for`
   copies in test files should converge on the shared helper.
2. **Assert the invariant, not the intermediate state.** When a race makes
   several intermediate states legal, assert only the invariant (the
   FLAKY-009 fix is the canonical example: exactly one pending row,
   whichever request won).
3. **Mocks must return real shapes.** A default-`undefined` mock walks the
   error branch under CI timing and fails good code (#801's lesson).
4. **Budget timeouts for CI load.** Set timeout values against the loaded
   2-core runner, not the dev machine — or better, anchor on the signal
   and not on time at all (rule 1 subsumes this when achievable).

The postgres tier's CI `-n 1` pin (above) removes the resource contention
that produced the family; these rules keep new tests from reintroducing it.

The postgres tier shards are a deterministic `md5(nodeid) % 3` collection
filter (`scripts/pytest_gate_shard.py`, `GATE_SHARD=i/n`). Every pytest shard
writes its own `COVERAGE_FILE` with `--cov-fail-under=0`, so only the
combined report in backend-coverage enforces the 85% floor.

CI environment notes:

- The workflow declares `permissions: contents: read`; PR test code and build
  scripts receive no write token, and release workflows grant their own
  permissions separately.
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

1. Protect `main` via the **repository ruleset `trunk-protection-main`** (Settings → Rules → Rulesets): required status check `quality-gate`, deletion and non-fast-forward blocked. It replaced the legacy branch-protection object (#1150); the nightly exemption-expiry job's manifest refresh merges through an auto-merge PR on the docs-only lane instead of a direct push — the Actions bot has no clean Integration bypass on personal repos, and the PR path keeps the trunk ruleset intact. `release/*` trains get the same ruleset shape when they exist.
2. Require only the stable `quality-gate` status check before merging. It
   validates selected internal lanes, including `docs-terms` for docs-only
   changes; do not require volatile shard names individually.
3. On `main`, enable Merge Queue after the workflow contains the
   `merge_group: checks_requested` trigger. The queue validates the synthetic
   combined commit rather than relying on independently green, stale PR heads.
4. Disable force-push and branch deletion for protected branches (covered by
   the ruleset's non_fast_forward + deletion rules).
5. Merge changes through a pull request; do not edit protected branches in the web UI.

Until required status checks are configured, nothing server-side blocks a red
merge — the protection is only as strong as this one-time setup.

A per-job required-context list drifts silently: renaming or sharding a job
leaves a ghost context that no run reports, and every PR waits forever on
"Expected" (#642). The branch-protection settings live outside the repo, so
no CI test catches this — require only the `quality-gate` aggregate (rule 2),
or update the per-job list in the same change as any workflow rename.

## Extended Gate Policy

The `ci-extended` CI job covers the areas that previously required a manual
`scripts/check-ci.sh` run:

- PostgreSQL schema migration, backup, or restore;
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
