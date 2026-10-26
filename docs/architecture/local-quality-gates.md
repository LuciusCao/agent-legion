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
| PR to `main`/`master`/`release/*`, push to `main`/`master` | Full tiers in selected CI lanes | Changed-path-selected CI lanes run in parallel; stable aggregate check `quality-gate` is the merge boundary |
| Weekly schedule, manual dispatch | Extended | CI jobs `ci-extended` + `nightly-e2e` + `exemption-expiry` + `deps-audit` (`nightly-gate.yml`) |

The pre-push hook diffs the pushed commits against their remote base and runs
only the affected quick-gate lanes locally: frontend-only changes skip the
backend pytest lane, docs-only changes run static checks only, and
backend-only changes skip Vitest. Ordinary mixed known paths take the union of
their lanes. Shared/global files (`pyproject.toml`, `uv.lock`, `scripts/`,
`.github/`, `config/`, …), new branches/tags, diff failures, and clean-tree or
otherwise indeterminate local invocations fall back to all lanes. Only
`docs/**`, repository-root `*.md` and `LICENSE` count as docs: nested markdown
such as the Studio bootstrap prompt, MCP guides or example `SKILL.md` files is
a runtime input and runs its directory's lane, and `velites/schema/**` also
runs the backend lane (Python contract tests read it). The rule lives in
`scripts/lane-paths.sh`, sourced by the CI `changes` job, `check-quick.sh` and
the pre-push hook, and `tests/scripts/test_lane_paths.py` pins all three to
one path table (#941). CI applies its own changed-path selection: selected
backend jobs run the complete unit/PostgreSQL layers rather than smoke, while
other selected lanes run their own checks. Local trimming therefore does not
reduce server-side coverage for applicable changes.

Lane trimming never covers the repo-wide governance checks (issue #1201):
`check_invariants`, `check_versions`, `check_architecture` and
`generate_architecture --check` govern frontend, velites and docs files too
(frontend file budgets, the `*.test.ts` 1000-line cap, version manifests,
docs freshness), but they run inside the backend lane's static phase — a
frontend-only trim would silence them locally (PR #1177 shipped two
over-ceiling frontend files through two green local gates). When the backend
lane is trimmed away, `check-quick.sh` runs the set alongside the static
round via `BACKEND_GATE_PHASE=governance`; when the lane is on, its static
phase already includes the set, so it runs exactly once either way
(`GATE_SKIP_GOVERNANCE=1` is the `check.sh` frontend+rust segment's dedup
knob, same idiom as `BACKEND_SKIP_WORKER_UI_TESTS`). Within the set, failures
aggregate — every section reports before the phase fails, so one violation
cannot mask the next. CI covers the same gap with the `governance-guard`
job (see below), the exact complement of `backend-unit`'s static step.

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
between gates. Measured on an idle machine (2026-10-09, 10-core dev box,
8 xdist workers): the backend unit tier alone (~5,100 tests) takes ~2m20s,
and the PostgreSQL tier (~3,900 tests) ~5m — yet the unit tier stretched
past 10 minutes inside a fully parallel gate.
The static round stays fully parallel (lint/typecheck are light), and the
`test` round inside `check-quick-frontend.sh`/`run_rust_round` lanes is
unaffected when invoked standalone.

The smoke tier (`GATE_TIER=smoke`) replaces only the backend **pytest**
selection with a curated subset. The backend/frontend/Rust static rounds,
worker UI tests, OpenAPI contract check, frontend tests and Rust tests still
run; smoke is not a docs-only or backend-only gate. Backend smoke runs without
coverage because the 85% floor only applies to the full suite. The ~90-second
soft target applies to the curated backend pytest invocation, **not** to the
complete gate's wall time.

`tests/conftest.py` marks a collected test as smoke when its path is in
`config/architecture/smoke-test-files.json` or its basename starts with
`test_architecture_`; pytest then selects those items with `-m "smoke"`. At
SHA `dfd129c1399a2a40662fd7d3b45a0188e3410b72`, the manifest's behavioral
files are:

- `tests/db/test_retry.py`
- `tests/executors/leases/test_claim_basics.py`
- `tests/executors/test_shard_contract.py`
- `tests/routes/jobs/test_job_lifecycle.py`
- `tests/routes/test_auth_routes.py`
- `tests/services/test_vault.py`
- `tests/workflows/test_sharding.py`

When adding coverage for a new subsystem, prefer one core behavioral file in
the manifest over weakening the backend smoke time target. Membership changes
must be reviewed against the actual selection rather than assuming that the
manifest alone lists every smoke test.

### Measured local smoke: one cold/warm pair

These are observed samples from 2026-10-26, not an SLA or a promised expected
time. The base was `dfd129c1399a2a40662fd7d3b45a0188e3410b72` plus the
documentation-only edits for this guide; no source or gate logic changed. The
host was a Mac16,12 (10 cores, 24 GiB) running macOS 15.6, uv 0.11.21, Node
26.3.0 and cargo 1.98.0. Pytest used eight xdist workers and Vitest used four
workers. Backend coverage was off in both runs.

Before the cold run, `scripts/init-worktree.sh` had pre-warmed `.uv-cache`;
`./scripts/ensure-frontend-deps.sh` installed `frontend/node_modules` in
15.11s. Cold means `.venv` and `velites/target` were absent (remove only those
disposable directories in a private timing worktree, never a shared checkout),
with the initialized uv cache and frontend dependencies retained. Warm means
an immediate rerun in the same worktree without cleanup. Both complete runs
used:

```bash
/usr/bin/time -p env \
  GATE_LANES="backend frontend rust" GATE_TIER=smoke \
  AGENT_LEGION_GATE_OUTPUT_LINES=0 \
  PYTEST_ADDOPTS="--durations=10 --junitxml=/tmp/smoke-backend-junit.xml" \
  ./scripts/check-quick.sh
```

The machine-wide queue was at its default of one. Neither measured complete
run waited for a slot; the independent lane measurements for CONTRIBUTING
likewise exclude any announced queue wait and use the gate's execution timer.
`/usr/bin/time` supplies total wall time, pytest/Vitest supply their own test time, and
one-second process samples delimit the other warm phases. Static lanes and the
frontend/Rust test lanes overlap, so phase rows do not sum to total wall time.

| Step | Cold | Warm |
| --- | ---: | ---: |
| Frontend dependency setup (`npm ci`, outside gate) | 15.11s | not rerun |
| Backend static lane | not separately recorded | 49s |
| Frontend static lane | not separately recorded | 50s |
| OpenAPI contract step | not separately recorded | 5s |
| Backend test phase (worker UI + curated pytest) | not separately recorded | 85s |
| Backend curated pytest alone (642 tests, no coverage) | 447.07s | 80.74s |
| Frontend Vitest (3,308 tests) | 1,268.00s | 519.30s |
| Complete smoke gate (`/usr/bin/time`) | 2,105.90s | 667.03s |

Cold Rust static compilation reported 2m59s; the warm Rust test command's
compilation stage reported 0.56s before its test binaries ran. Those are stage
times, not complete Rust-lane times; use the independently measured lane table
in CONTRIBUTING for the latter. The cold run passed all lanes, as did the warm
run. Cold backend pytest exceeded the ~90-second soft target and warm backend
pytest met it; neither result says anything about the complete gate meeting
that target.

This is one machine, one pair of runs, with diagnostic process sampling and
uncontrolled unrelated host load. It is not a controlled benchmark or a
confidence interval. Unrecorded phases are unknown, not zero. Reuse the
commands to obtain a new local sample instead of treating these numbers as a
budget; CI full-unit/PostgreSQL telemetry measures a different tier and was
not substituted for these smoke results.

### Smoke slow-test attribution

The warm sample's `--durations=10` report separates fixture costs from test
bodies. Class A is the session-scoped `real_repo_check_errors` full-repository
scan running once in each of two xdist workers (filesystem checks plus
Git/helper subprocesses, not a fixed-sleep test). Class B is the
per-xdist-worker PostgreSQL session schema rebuild charged to that worker's
first database test. Class C is a private `create_app` + `TestClient` and
several database-backed API requests; these four call-phase test bodies have
no fixed-sleep or explicit external-subprocess call (the `pi` argv in one test
is stored as data, not run). JUnit's combined case time is a different metric and
must not be substituted for this phase breakdown.

| Rank | Pytest nodeid | Phase/time | Class |
| ---: | --- | ---: | --- |
| 1 | `tests/scripts/test_architecture_video_legacy.py::test_video_legacy_current_repository_has_no_errors` | setup 56.43s | A |
| 2 | `tests/scripts/test_architecture_phase6.py::test_phase6_current_repository_has_no_errors` | setup 54.19s | A |
| 3 | `tests/executors/leases/test_claim_basics.py::test_two_workers_claim_one_node_only_one_succeeds` | setup 6.47s | B |
| 4 | `tests/routes/test_auth_routes.py::test_bootstrap_only_available_for_first_user` | setup 5.27s | B |
| 5 | `tests/routes/jobs/test_job_lifecycle.py::test_job_detail_includes_node_inputs_outputs` | call 5.18s | C |
| 6 | `tests/routes/jobs/test_job_lifecycle.py::test_job_detail_includes_pi_run_trace` | call 4.80s | C |
| 7 | `tests/workflows/test_sharding.py::test_aggregate_all_pending` | setup 4.26s | B |
| 8 | `tests/routes/jobs/test_job_lifecycle.py::test_job_detail_includes_pi_run_trace` | setup 3.93s | B |
| 9 | `tests/routes/jobs/test_job_lifecycle.py::test_list_workspace_runs_filters_by_status_and_node` | call 3.89s | C |
| 10 | `tests/routes/jobs/test_job_lifecycle.py::test_get_job_detail_and_artifact_when_enabled` | call 3.87s | C |

For class A, run the real-repository assertion once outside xdist or share
its immutable scan result across workers; keep the fast temporary-repository
cases in smoke. For class B, profile and reduce shared schema initialization
(or safely share its immutable setup) before changing test selection. For
class C, profile app construction, authentication and request seeding before
reusing more app state; do not trade away the isolation that these private-app
tests rely on. None should be marked `slow` or moved out of smoke merely from
this single sample. Re-measure phase durations after any optimization.

The cold top10 contained only setup entries: class A dominated at 196.05s and
195.36s, while eight class-B entries ranged from 38.63s to 62.74s. That is
consistent with cold filesystem and schema initialization, not with ten
newly discovered sleep-dependent test bodies.

The unit tier (`GATE_TIER=unit`) runs the complete PostgreSQL-offline unit
layer, selected with `-m "not postgres"` against an
unreachable loopback database URL, so an accidental database dependency fails
the gate instead of silently using a developer database. CI runs it as the
`backend-unit` job; the PostgreSQL integration layer (`GATE_TIER=postgres`)
runs in the `backend-postgres` matrix job (checks `backend-postgres-a/b/c`)
described below.

The full tier (`GATE_TIER=full`, the default for `check-quick.sh` without a
tier override) selects the same unit layer as `GATE_TIER=unit`: the
PostgreSQL integration layer (~3,900 of the quick suite's ~9,000 tests and
~2.2x the unit tier's wall time) moved out of the local default because CI
re-runs all of it whenever the backend lane is selected — paying it on every
local gate bought little. Database
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
run with `--cov-context=test`, ~2.5 minutes per worktree), and the frontend lane runs `vitest related`
over the changed frontend files. It falls back to the plain unit tier when
no index exists, when a changed source file is missing from the index (an
index blind spot — the affected tests are unknown), or when the selection
would run most of the suite anyway — the fallback never selects beyond the
plain unit tier.
A genuinely empty selection (nothing in the tracked change set touches the
unit tier — postgres-tier-only edits, `tests/full` / `tests/ci` files, docs)
is skipped explicitly instead of failing on pytest's exit 5; the dependency
manifests (`pyproject.toml` / `uv.lock`) and backend-affecting uncovered trees
(`config/` / `deploy/` / `examples/`) stay visible to the selector and always
take the full-fallback path, so neither a dependency upgrade nor one of those
changes skips the backend tests. New or already tracked
`server/app/routes/**` files missing from the index likewise take the full
fallback: their route-walking inventory tests may belong to the postgres
tier, so selecting one in aff could otherwise degrade to a zero-test pass.
Deleted test files are dropped from the
selection (a stale path would fail pytest collection). An aff pass
is **not** gate evidence: `scripts/run-local-gate.sh` rejects the tier.
Pre-push runs its selected non-aff gate (smoke by default, or the explicitly
requested quick/full gate); CI runs the complete unit and PostgreSQL layers
for applicable paths. Rebuild the index after
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
repository-root `*.md`, `LICENSE`) still trigger the workflow, but the
backend, frontend and Rust flags evaluate to false in the `changes` job and
those lanes skip without acquiring runners, including the complete
`backend-postgres` matrix. The `docs-terms` and `governance-guard` checks
still run on that path. Docs-only and mixed documentation changes can
reintroduce retired terminology into current-state docs; because docs-only
changes have no `backend-unit` static step, the retired-terms check gets its
own lightweight job whenever the backend lane is off (`backend-unit` runs it
inside `check_architecture` whenever that lane is on — exact complements).
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
  unless its exact nodeid has a registry entry. An untouched, pre-existing
  expired entry is only a note here and does not make an unrelated PR red; the
  nightly `exemption-expiry` job enforces expiry. For each PR or merge-group
  run of this job (any target branch), the target branch's registry
  (`--base-registry`) is also checked: entries the change adds or re-targets
  (including re-dating, nodeid, scope, recurring, registered_on / extended_on
  changes — #1034) fail if their deadline is already expired. Scheduled jobs
  see only the default branch, and the nightly check runs only after merge.
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
- **governance-guard** — the repo-wide governance checks
  (`BACKEND_GATE_PHASE=governance`: `check_invariants`, `check_versions`,
  `check_architecture`, `generate_architecture --check`) as a standalone job
  (issue #1201). They govern frontend/velites/docs files too, so they must
  run even when a path trim turns the backend lane off; when the lane is on,
  `backend-unit`'s static phase already includes them (exactly once either
  way). Needs the full project env (`check_architecture` imports
  `server.app`) and full history (budget monotonicity anchors on `HEAD^`),
  and carries the same release-train anchor opt-out as `backend-unit`.
- **docker-build** — CI-only image build lane (host + worker targets). It runs
  only when the `changes` job detects `Dockerfile`, `.dockerignore`,
  `pyproject.toml`, `uv.lock`, `worker/**`, `shared/**` or `deploy/**`;
  `frontend/package.json`, `frontend/package-lock.json` and
  `velites/Cargo.lock` do not enable this lane under the current filter. No
  other job exercises the Dockerfile.
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
  fails the job so the exemption gets fulfilled or re-anchored first. The
  `config/architecture/issue-states.json` PR takes the path-selected
  backend+frontend quality gate; standalone `docs-terms` and
  `governance-guard` skip because backend is selected. The selected
  `backend-unit` static phase carries governance and retired-terms checks, so the bot
  does not bypass the trunk ruleset. Since #295 it also detects expired
  flaky-registry deadlines (`check_reruns.py --check-deadlines`, without
  needing extended rerun evidence), and checks maintained unreleased branches
  (`scripts/quality/flaky_branch_deadlines.py`: every `release/*` without its
  corresponding release tag, including unversioned names; registries are read
  leniently from fetched branch tips). The nightly deadline-only checks
  enforce every expired non-recurring entry and warn seven days ahead.
  PR/merge-group backend-coverage checks registration for all observed
  reruns, but enforces expiry only for entries the change adds or re-targets;
  untouched, pre-existing expired entries remain notes and do not fail
  unrelated PRs.
  The registry's clock-free rules (one entry per nodeid, deadline at most 45
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
- Jobs that need PostgreSQL get a fresh `postgres:17` service container;
  `AGENT_LEGION_DATABASE_URL` and `AGENT_LEGION_TEST_DATABASE_URL` point at it.
  The test database and worker schemas are created lazily when the PostgreSQL
  layer starts; importing the test support module and running the unit layer
  never connects to PostgreSQL.
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

1. Protect `main` via the **repository ruleset `trunk-protection-main`**
   (Settings → Rules → Rulesets): required status check `quality-gate`,
   deletion and non-fast-forward blocked. It replaced the legacy
   branch-protection object (#1150). The nightly exemption-expiry job's
   manifest refresh merges through an auto-merge PR's path-selected
   backend+frontend quality gate instead of a direct push — the Actions bot
   has no clean Integration bypass on personal repos, and the PR path keeps
   the trunk ruleset intact. `release/*` trains get the same ruleset shape
   when they exist.
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
  run as parallel CI jobs whenever the backend lane is selected.
- For applicable PR/push paths, CI adds the PostgreSQL and full-gate evidence
  layers, so database and cross-control-plane regressions are still caught
  server-side before merge.
- Stress evidence runs weekly instead of on every push, trading same-day
  detection for a much cheaper push loop; risky changes can trigger it on
  demand via `workflow_dispatch`.
- Hooks can still be bypassed with `--no-verify`; the required status checks
  on GitHub are the actual merge boundary.
- CI runs in a clean environment (fresh Postgres, no local skill repos, no
  `.env`), which also proves the gates are environment-independent.
