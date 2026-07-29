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
| Push (any branch) | Quick | `scripts/check-quick.sh` |
| PR / push to `develop`, `main`, `master` | Full | CI jobs `backend` + `frontend` |
| Push to protected branches, manual dispatch | Extended | CI job `ci-extended` |

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
`develop` / `main` / `master`, plus manual dispatch:

- **backend** — static checks (ruff, format, mypy, architecture contracts,
  invariant registry, spec health), the full pytest suite with coverage, and
  the `tests/full -m full_gate` evidence with a combined coverage report. This
  is the backend lane of `scripts/check.sh`.
- **frontend** — generated API contract, prettier, ESLint, `tsc`, Vitest with
  coverage, and the production bundle (`npm run build:bundle`).
- **ci-extended** — `tests/ci -m ci_extended` stress scenarios. Skipped on pull
  requests; runs on branch pushes and manual dispatch, matching the old manual
  `scripts/check-ci.sh` policy.

CI environment notes:

- Each job gets a fresh `postgres:17` service container; `AGENT_LEGION_DATABASE_URL`
  and `AGENT_LEGION_TEST_DATABASE_URL` point at it. The test database is created
  automatically by `tests/postgres_support.py`.
- The frontend job also needs Python + Postgres because `npm run api:check`
  regenerates the OpenAPI schema through `create_app`.
- `AGENT_LEGION_SKIP_SKILLS_SHARED_CHECK=1` skips `check-skills-shared.py` in CI:
  `config/skills.yaml` points at machine-local skill repos (`~/.agents/skills/...`)
  that do not exist on runners. Local gates still run the check.
- uv and npm caches are enabled; the first cold run is dominated by downloading
  torch/funasr and takes substantially longer than cached runs.

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

Because the job runs on every push to protected branches, no manual step is
needed anymore; use `workflow_dispatch` to run it against any other ref. If a
deterministic test cannot run in the CI environment, record the exact failure
in the pull request and rerun where the required resource is available. Do not
record passing evidence for a partial gate.

## Quality Impact

- Fast feedback remains cheap enough to run on every commit; pushes only wait
  for the quick gate locally.
- The slow full gate and stress evidence run on every PR/push server-side,
  instead of blocking the maintainer machine or relying on manual runs.
- Hooks can still be bypassed with `--no-verify`; the required status checks
  on GitHub are the actual merge boundary.
- CI runs in a clean environment (fresh Postgres, no local skill repos, no
  `.env`), which also proves the gates are environment-independent.
