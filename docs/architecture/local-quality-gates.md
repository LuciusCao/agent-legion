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
| PR / push to `develop`, `main`, `master` | Full | CI jobs `backend` + `frontend` |
| Nightly schedule, manual dispatch | Extended | CI job `ci-extended` |

The pre-push hook diffs the pushed commits against their remote base and runs
only the affected quick-gate lanes locally: frontend-only changes skip the
backend pytest lane, docs-only changes run static checks only, and
backend-only changes skip Vitest. New branches/tags, shared files
(`pyproject.toml`, `uv.lock`, `scripts/`, `.github/`, `config/`, …), mixed
diffs, and any diff failure fall back to all lanes. CI always runs every lane
of the full quick suite, so trimming never weakens the server-side boundary.
The lane set and the test tier are part of the local evidence fingerprint, so
evidence from a trimmed run is never reused for a different lane set or tier.

The smoke tier (`GATE_TIER=smoke`, also accepted as `unit`) runs the complete
PostgreSQL-offline unit layer, selected with
`-m "not postgres and not repository_gate"`. It points the test database URL
at an unreachable loopback port, so an accidental database dependency fails
the gate instead of silently using a developer database. It runs without
coverage locally because the 85% floor applies to the combined CI suite, and
must remain under the 90-second pre-push budget.

Install the repository-managed hooks once from a worktree that contains `.githooks/`:

```bash
make install-hooks
```

The installer copies small dispatchers into the Git common hooks directory. A dispatcher resolves
the current worktree root and executes its versioned `.githooks/` implementation. If an older
branch does not contain `.githooks/`, the dispatcher exits successfully and leaves that worktree
unaffected. Passing evidence is shared through the same Git common directory.

## CI Workflow

`.github/workflows/quality-gate.yml` runs on pull requests and pushes to
`develop` / `main` / `master`, nightly (schedule), plus manual dispatch:

- **backend** — static checks (ruff, format, mypy, architecture contracts,
  invariant registry, spec health), a PostgreSQL-offline unit layer, a
  PostgreSQL integration layer, and the `tests/full -m full_gate` evidence.
  Coverage is appended across all three layers and enforced on the combined
  report. This is the backend lane of `scripts/check.sh`.
- **frontend** — generated API contract, prettier, ESLint, `tsc`, Vitest with
  coverage, and the production bundle (`npm run build:bundle`).
- **ci-extended** — `tests/ci -m ci_extended` stress scenarios. Runs only on
  the nightly schedule and manual dispatch; PR and push runs skip it.

CI environment notes:

- Each job gets a fresh `postgres:17` service container; `AGENT_LEGION_DATABASE_URL`
  and `AGENT_LEGION_TEST_DATABASE_URL` point at it. The test database and worker
  schemas are created lazily when the PostgreSQL layer starts; importing the
  test support module and running the unit layer never connects to PostgreSQL.
- The frontend job also needs Python + Postgres because `npm run api:check`
  regenerates the OpenAPI schema through `create_app`.
- `AGENT_LEGION_SKIP_SKILLS_SHARED_CHECK=1` skips `check-skills-shared.py` in CI:
  `config/skills.yaml` points at machine-local skill repos (`~/.agents/skills/...`)
  that do not exist on runners. Local gates still run the check.
- uv and npm caches are enabled; the first cold run is dominated by downloading
  torch/funasr and takes substantially longer than cached runs.

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
2. Require the `backend` and `frontend` status checks to pass before merging;
   require branches to be up to date.
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

The job runs on the nightly schedule, so no manual step is needed for routine
work; use `workflow_dispatch` to run it against any ref before a risky merge
(schema migrations, executor concurrency, filesystem deletion, release
tags). If a deterministic test cannot run in the CI environment, record the
exact failure in the pull request and rerun where the required resource is
available. Do not record passing evidence for a partial gate.

## Quality Impact

- Fast feedback remains cheap enough to run on every commit; pushes wait for
  the complete PostgreSQL-offline unit layer rather than a curated file sample.
- CI adds the PostgreSQL and full layers on every PR/push, so database and
  cross-control-plane regressions are still caught server-side before merge.
- Stress evidence runs nightly instead of on every push, trading same-day
  detection for a much cheaper push loop; risky changes can trigger it on
  demand via `workflow_dispatch`.
- Hooks can still be bypassed with `--no-verify`; the required status checks
  on GitHub are the actual merge boundary.
- CI runs in a clean environment (fresh Postgres, no local skill repos, no
  `.env`), which also proves the gates are environment-independent.
