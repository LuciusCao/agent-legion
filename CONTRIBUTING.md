# Contributing to Agent Legion

Thanks for your interest in contributing. This document describes the minimal
development workflow; the binding engineering rules live in
[AGENTS.md](AGENTS.md) (worktree isolation, boundary rules, security and data
red lines) — read it before non-trivial changes.

## Development setup

Prerequisites: Python 3.11+, Node 20.19+ or 22.13+ (22 recommended; ESLint and Vitest engine ranges exclude
21 and 23; CI uses 22),
PostgreSQL 17, [`uv`](https://docs.astral.sh/uv/), a Rust toolchain (builds
the `velites` binary), Docker (local SeaweedFS object storage; optional at
runtime — without it materials APIs return 503 — but required by
`make install` on non-macOS platforms), and `openssl`. On macOS with Homebrew
`make install` installs whatever is missing; on other platforms install them
first — `make install` checks each one and fails fast with guidance.

```bash
make install    # deps, uv sync, agent_legion_dev database, .env with random
                # local S3 credentials, vault master key, velites build,
                # frontend deps, worker state copy — idempotent
make dev-up     # local SeaweedFS + backend :8001 + console :5174 + worker :8789
```

`make install` writes the vault master key to `deploy/secrets/vault_master_key`
but does not point `.env` at it; the native backend reads the key only from
env, so add `AGENT_LEGION_VAULT_MASTER_KEY_FILE=<absolute path to that file>`
to `.env` before saving secrets or external-service connections (without a
key the server starts, but vault writes fail).

See [README.md](README.md) for the first-run walkthrough and the demo
workflow. Additional git worktrees are initialized with
`scripts/init-worktree.sh` right after creation (copies `.env`, derives a
per-worktree database and bucket; see AGENTS.md §1), not with `make install`.

### Manual setup (what `make install` does)

Only needed when you want to run the steps by hand; they mirror
`scripts/install-deps.sh`:

```bash
# Intel Mac only: cryptography builds from source (#1089)
export OPENSSL_DIR="$(brew --prefix openssl@3)"   # after `brew install openssl@3 rust`
uv sync
# start PostgreSQL first (e.g. `brew services start postgresql@17`), then:
createdb agent_legion_dev            # NOT the bare `agent_legion` name — init_db
                                     # refuses to migrate it without
                                     # AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA=1
cp .env.example .env && chmod 600 .env
# .env: fill AGENT_LEGION_S3_ACCESS_KEY / AGENT_LEGION_S3_SECRET_KEY
#       (e.g. `openssl rand -hex 20` / `openssl rand -hex 40`); the local
#       SeaweedFS container is created with these credentials. The
#       template's AGENT_LEGION_DATABASE_URL already points at agent_legion_dev.
mkdir -p deploy/secrets
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
  > deploy/secrets/vault_master_key && chmod 600 deploy/secrets/vault_master_key
# .env: AGENT_LEGION_VAULT_MASTER_KEY_FILE=<absolute path to that file>
./scripts/ensure-velites.sh --dest data/bin   # build velites (needs cargo)
./scripts/ensure-frontend-deps.sh             # npm ci when the lockfile changed
```

`scripts/install-deps.sh` also seeds the worker state copy
`data/agent-worker-service/worker.yaml`; read its final step for the minimal
dev values.

## Running the tests for the first time

The edit-test inner loop selects affected backend tests from a local coverage
index, so build the index once per checkout/worktree (and again after
dependency or `tests/conftest.py` changes):

```bash
GATE_TIER=aff-index ./scripts/check-quick-backend.sh   # one-off, a few minutes
GATE_TIER=aff ./scripts/check-quick.sh                 # inner loop
```

The test database is derived and created automatically
(`tests/postgres_support.py`); set `AGENT_LEGION_TEST_DATABASE_URL` only to
override it. Gate tiers, the machine-wide gate queue, and what CI runs are
documented in
[docs/architecture/local-quality-gates.md](docs/architecture/local-quality-gates.md).

## Before you open a PR

1. Run the affected-test feedback loop while editing
   (`GATE_TIER=aff ./scripts/check-quick.sh`). It selects backend tests from
   the local coverage index and uses `vitest related` for frontend changes;
   missing or stale selection evidence falls back to the complete unit tier.
   An aff pass is feedback, not a merge credential.

2. Install the versioned local hooks once (`make install-hooks`): pre-commit
   runs the fast gate, pre-push runs the smoke tier trimmed by the pushed
   paths. Never bypass them with `--no-verify`.

3. The full gate runs on GitHub Actions for every PR
   (`.github/workflows/quality-gate.yml`). The stable `quality-gate` aggregate
   check is the required merge boundary; run `./scripts/check.sh` locally only
   when CI is unavailable or an offline release credential is required.

### PR target branch

- Issue PRs target the release integration branch of the version in
  progress (`release/<version>`, e.g. the one recent merged PRs point at);
  the release branch merges into `main` when the version is cut. Several
  `release/*` branches can exist at once — ask a maintainer when unsure
  which one is open.
- Stacked PRs: express the dependency with the child PR's base branch; fix
  shared defects in the lowest affected layer first, then merge bottom-up
  (re-target the child onto the release branch and re-run CI on its new head
  before merging it). The PR gate only triggers for PRs into trunk and
  `release/*` branches, so dispatch `quality-gate.yml` manually for a PR
  whose base is a feature branch. This repository squash-merges: after a
  parent PR lands, the child's inherited parent commits are no longer
  ancestors of the release branch, so check the merge-base and diff (and
  replay only the child's own commits if needed) before re-targeting it.

## House rules

- Keep changes minimal and scoped; match the surrounding code style.
- Frontend transport types are generated from the backend OpenAPI schema
  (`make api-generate`) — never hand-write them.
- New tests go into the subsystem subdirectory under `tests/` (e.g.
  `tests/services/`, `tests/routes/`), not the `tests/` root.
- Split test files proactively once they pass 800 lines (the gate's 1000-line
  cap is a hard floor to stay clear of): moving cases untouched into sibling
  files by theme beats being blocked at the gate right before handoff.
  Existing files over the line are not expected to be paid down at once —
  split them the next time you touch the file.
- Secrets never enter tracked config files, the database, API responses, or
  logs — use the vault / env channels described in AGENTS.md §8.
- Architecture boundary changes must be reflected in
  `config/architecture/` (invariants, exemptions, budgets); see AGENTS.md §5.
- Versioning is decoupled per component: `velites/Cargo.toml` and
  `frontend/package.json` carry their own version lines and must **not** be
  bumped alongside the repository version (`pyproject.toml`). Advance them
  only when the component itself changed since its last version bump —
  `scripts/check_versions.py` (part of the backend static round) rejects a
  lockstep bump with no source changes behind it, because the velites binary
  freshness fingerprint (`ensure-velites.sh`) and the Docker layer cache key
  off the component's source tree. Lockfile versions must stay in sync with
  their manifest (`uv lock` / `cargo update -w` /
  `npm install --package-lock-only`).

## Reporting issues

Use the GitHub issue templates. Include the gate output or server logs when
reporting a bug, and redact any credentials before pasting.
