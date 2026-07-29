# Agent Legion

Agent Legion is a self-hosted console that turns AI agents into a managed
production line for educational content. You define a workflow as a DAG of
**capabilities**, submit a batch of work (video URLs, knowledge codes, question
IDs), and Agent Legion schedules every node across local and remote agent
executors — with a live UI to watch, rerun, and package the results.

It ships with two production workflows:

- **`video_knowledge`** — turn raw knowledge videos into structured teaching
  packages: download → transcribe → subtitle review → chaptering → interaction
  design → content review → assemble → package.
- **`question_comprehension_info`** — generate structured comprehension
  metadata for math word problems: fetch from CMS → parse → eligibility
  classification → key-info & error generation/review → difficulty scoring →
  assemble.

## Features

- **Workspace-scoped DAG workflows.** Workflows are YAML files in
  `config/workflows/`; nodes declare only a business `capability` and their
  input/output artifacts — never how to run them. Rerun a single node, run to
  a target node, or continue from a pause; downstream staleness is tracked
  automatically.
- **Batch intake.** Create job batches through
  `POST /api/workspaces/{id}/job-batches`: submit video URLs directly, or
  resolve videos/questions from the CMS by knowledge code or ID list.
- **Pluggable agent runtimes.** Agent nodes run headlessly through the Pi CLI
  or **velites** — Agent Legion's own Rust harness (&lt;50 ms cold start vs
  Pi's ~1.6 s, a fraction of the memory, same pi-compatible event stream).
  Switch per agent with the `runtime` field (`pi` / `openclaw` / `velites`) in
  the agent definitions of `config/workflow.yaml`; `workflows.pi.flavor` is a
  legacy selector that only applies to `runtime: pi` agents.
- **Versioned external skills.** Each capability maps to a skill in a
  standalone git repository, declared in `config/skills.yaml` and pinned by
  `config/skills.lock`. Every run restores the locked ref, so workflow output
  is reproducible.
- **Local & remote executors.** Capacity is granted through executor leases;
  remote **Agent Workers** register over HTTP, claim executions, stream
  heartbeats, and upload artifacts — scale out by adding machines.
- **Real-time console.** React SPA with a live DAG view (React Flow), SSE
  dashboard events, WebSocket agent status, run logs, artifacts, token-usage
  statistics, and failure-category batch rerun.
- **Local ASR.** Subtitle transcription via whisper.cpp with automatic
  fallback to SenseVoice when the result is missing, too short, or degenerate.
- **Secrets vault.** Workspace secrets (CMS tokens, `secret: true` binding
  fields) are Fernet-encrypted at rest; configs and snapshots carry only
  `secret_ref` — never plaintext.
- **Multi-user with workspace ACL.** Cookie sessions + CSRF guard, admin user
  management, per-workspace editor/viewer membership.
- **PostgreSQL control plane.** One authoritative database coordinates
  multi-process and multi-machine scheduling; artifacts and run traces live
  under `data/`.

## Architecture

```
Browser (React SPA)
   │ REST / SSE / WebSocket
   ▼
FastAPI Host ─────────────────────────────────────────┐
   │ routes → services → workflows (DAG scheduler)    │
   │                       │ executor leases          │
   ▼                       ▼                          ▼
PostgreSQL          Local executors            Agent Workers (remote)
(control plane)     pipeline nodes             claim → run → artifacts
   │                       │                          │
   ▼                       ▼                          ▼
data/  (videos, logs, packages, jobs, run traces)
        agent nodes → Pi CLI / velites → external skills (git, locked)
```

- **Backend**: Python 3.11+, FastAPI, Uvicorn, PostgreSQL
- **Frontend**: React 18, TypeScript, Vite, Zustand, MUI v6, React Flow
- **Agent harness**: velites (Rust, `velites/`) or Pi CLI (Node)
- **Tooling**: `uv` + Ruff + mypy (Python), npm + ESLint + Prettier (frontend),
  pytest + Vitest + cargo test

Key design rules (enforced by architecture checks, see
[AGENTS.md](AGENTS.md) and [docs/architecture/](docs/architecture/)):

- Workflow nodes declare `capability` only — runner, agent, and skill wiring
  live in the executor layer.
- Routes are thin HTTP adapters; business logic lives in services; executors
  acquire capacity exclusively through leases.
- Frontend transport types are generated from the backend OpenAPI schema
  (`frontend/src/generated/api.ts`), never hand-written.
- Secrets enter the vault or env only — tracked config yaml rejects secret
  values at startup.

## Quick Start

Prerequisites: Python 3.11+, Node 18+, PostgreSQL, [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync                                     # Python deps
createdb agent_legion
cp .env.example .env                        # fill in secrets
export AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion
cd frontend && npm install && cd ..
```

Run (two terminals):

```bash
# backend (binds 127.0.0.1 only — never expose with 0.0.0.0)
uv run uvicorn server.app.main:app --reload --reload-dir server --port 8000

# frontend dev server (proxies /api to the backend)
cd frontend && npm run dev
```

First start redirects to `/setup` to create the admin user. For production,
`cd frontend && npm run build` — the backend then serves the SPA from
`http://127.0.0.1:8000`.

Common tasks have Makefile shortcuts (`make help` lists all):

```bash
make dev-backend      # backend dev server
make dev-frontend     # frontend dev server
make check-quick      # quick quality gate (daily)
make check            # full quality gate (before handoff)
make api-generate     # regenerate frontend API types
make skills-lock      # refresh config/skills.lock
make install-hooks    # install pre-commit / pre-push gates
```

## Configuration

Config is split by domain under `config/`; each file owns a fixed set of
top-level keys and anything else fails startup:

| File | Owns |
|------|------|
| `config/app.yaml` | database URL, paths, HTTP, cleanup, monitoring |
| `config/agent_legion.yaml` | ASR, CMS, OpenClaw |
| `config/workflow.yaml` | agent catalog, agent workers, executors, Pi/velites runtime |
| `config/workflows/*.yaml` | workflow DAG definitions |
| `config/skills.yaml` + `skills.lock` | skill sources and pinned refs |

Secrets are never written to yaml: database URL comes from
`AGENT_LEGION_DATABASE_URL`, the vault master key from
`AGENT_LEGION_VAULT_MASTER_KEY[_FILE]`, the bootstrap admin password from
`AGENT_LEGION_BOOTSTRAP_ADMIN_PASSWORD`, and CMS tokens from `CMS_*` env vars
or the workspace vault. Full reference:
[docs/architecture/backend.md](docs/architecture/backend.md).

## Agent Runtimes

Agent nodes execute through external skills — standalone git repositories
containing `SKILL.md`, an output contract, and a validator — typically checked
out under `~/.agents/skills/agent-legion/<workflow>/<capability>/` and pinned
by `config/skills.lock`.

Two harness flavors run them:

- **Pi CLI**: `npm install -g --ignore-scripts @earendil-works/pi-coding-agent`,
  then `pi` to authenticate and `./scripts/check-pi.sh` to verify. Configure
  provider/model/timeout under `workflows.pi` in `config/workflow.yaml`. Pi is
  an optional runtime; the production default is velites.
- **velites** (production default): a single static Rust binary built from
  `velites/` (`cargo build --release`), emitting the same event stream the host
  consumes. Enable per agent by declaring `runtime: velites` in the agent
  definition in `config/workflow.yaml`; `workflows.pi.flavor` remains only as a
  rollback lever for `runtime: pi` agents (the video pipeline). See
  [docs/architecture/velites-harness.md](docs/architecture/velites-harness.md).

Every node execution leaves a full trace under
`{job_dir}/runs/{node_key}/{run_token}/` (prompt, event stream, stderr,
metadata), so any agent decision can be audited after the fact.

## Quality Gates

- `./scripts/check-quick.sh` — daily gate: Ruff, mypy, pytest, architecture
  invariant/contract checks, ESLint/Prettier/typecheck/Vitest, cargo
  fmt/clippy/test.
- `./scripts/check.sh` — full gate before handoff, with coverage floors
  (backend 85%) and the frontend production build; also runs on GitHub
  Actions for PRs and pushes.
- `make install-hooks` — versioned pre-commit (fast checks) and pre-push
  (smoke tier, lane-trimmed by pushed paths) hooks.

Details and CI policy:
[docs/architecture/local-quality-gates.md](docs/architecture/local-quality-gates.md).

Load testing harnesses for large agent fleets live in `scripts/stress/` and
`frontend/stress/` (synthetic event streams, workspace UI stress, end-to-end
browser runs).

## Documentation

- [docs/architecture/](docs/architecture/) — module-by-module architecture
  (backend, frontend, pipeline, deployment, project structure)
- [AGENTS.md](AGENTS.md) — operating rules and boundary constraints for AI
  agents working in this repo
- [docs/agent-worker-deployment.md](docs/agent-worker-deployment.md) — remote
  worker deployment
- [docs/remote-execution-runbook.md](docs/remote-execution-runbook.md) —
  remote execution operations
