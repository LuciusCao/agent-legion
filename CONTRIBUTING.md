# Contributing to Agent Legion

Thanks for your interest in contributing. This document describes the minimal
development workflow; the binding engineering rules live in
[AGENTS.md](AGENTS.md) (worktree isolation, boundary rules, security and data
red lines) — read it before non-trivial changes.

## Development setup

Prerequisites: Python 3.11+, Node 18+, PostgreSQL 17, and
[`uv`](https://docs.astral.sh/uv/).

```bash
uv sync                                     # Python deps
createdb agent_legion_dev                   # NOT the bare `agent_legion` name —
                                            # init_db refuses to migrate that one
                                            # without AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA=1
                                            # (the shared-database schema guard)
cp .env.example .env                        # adjust values as needed
export AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion_dev
cd frontend && npm install && cd ..
make dev-up                                 # backend + frontend + worker, background
```

See [README.md](README.md) for the full quick start and the demo workflow.

## Before you open a PR

1. Run the quick quality gate and keep it green:

   ```bash
   ./scripts/check-quick.sh
   ```

   It covers Ruff, mypy, pytest, architecture invariant/contract checks,
   ESLint/Prettier/typecheck/Vitest, and cargo fmt/clippy/test.

2. Optionally install the versioned local hooks (`make install-hooks`):
   pre-commit runs fast checks, pre-push runs a smoke tier trimmed by the
   pushed paths. Never bypass them with `--no-verify`.

3. The full gate runs on GitHub Actions for every PR
   (`.github/workflows/quality-gate.yml`); a red CI blocks merge.

## House rules

- Keep changes minimal and scoped; match the surrounding code style.
- Frontend transport types are generated from the backend OpenAPI schema
  (`make api-generate`) — never hand-write them.
- New tests go into the subsystem subdirectory under `tests/` (e.g.
  `tests/services/`, `tests/routes/`), not the `tests/` root.
- Secrets never enter tracked config files, the database, API responses, or
  logs — use the vault / env channels described in AGENTS.md §8.
- Architecture boundary changes must be reflected in
  `config/architecture/` (invariants, exemptions, budgets); see AGENTS.md §5.

## Reporting issues

Use the GitHub issue templates. Include the gate output or server logs when
reporting a bug, and redact any credentials before pasting.
