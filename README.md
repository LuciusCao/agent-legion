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

- **Workspace-scoped DAG workflows.** Built-in workflow DAGs are Python
  constants in `server/app/workflows/builtin.py`; nodes declare only a
  business `capability` and their
  input/output artifacts — never how to run them. Known workflow keys live in
  the DB `workflow_catalog` table: built-ins are seeded from the code
  constants at startup, and admins register new keys via
  `POST /api/workflows`. Rerun a single node, run to
  a target node, or continue from a pause; downstream staleness is tracked
  automatically.
- **Batch intake.** Create job batches through
  `POST /api/workspaces/{id}/job-batches`: submit video URLs directly, or
  resolve videos/questions from the CMS by knowledge code or ID list.
- **Pluggable agent runtimes.** Agent nodes run headlessly through the Pi CLI
  or **velites** — Agent Legion's own Rust harness (&lt;50 ms cold start vs
  Pi's ~1.6 s, a fraction of the memory, same pi-compatible event stream).
  Switch per agent with the `runtime` field (`pi` / `openclaw` / `velites`) in
  the Agent definition, managed in Studio「Agent 管理」(published into the
  `versioned_entities` table; yaml agent config is retired). Execution
  provider/model/thinking resolve from per-node Studio overrides, then the
  workspace Settings「Agent 默认配置」.
- **Versioned external skills.** Each capability maps to a skill in a
  standalone git repository, declared in the DB `global_settings`
  `skill_sources` document and pinned by the `skill_lock` document (managed
  via /admin/settings「Skill 源管理」or `make skills-lock`; the tracked
  `config/skills.yaml` / `config/skills.lock` files are retired). Every run
  restores the locked ref, so workflow output is reproducible.
- **Local & remote executors.** Capacity is granted through executor leases;
  remote **Agent Workers** register over HTTP, claim executions, stream
  heartbeats, and upload artifacts — scale out by adding machines.
- **Real-time console.** React SPA with a live DAG view (React Flow), SSE
  dashboard events, WebSocket agent status, run logs, artifacts, token-usage
  statistics, and failure-category batch rerun.
- **Local ASR.** Subtitle transcription via whisper.cpp with automatic
  fallback to SenseVoice when the result is missing, too short, or degenerate.
- **Secrets vault.** Workspace secrets (`secret: true` binding fields) are
  Fernet-encrypted at rest; configs and snapshots carry only `secret_ref` —
  never plaintext. Instance-level external service credentials (e.g. CMS)
  live on admin-managed connections (admin settings → 外部服务连接),
  Fernet-encrypted in `instance_secrets` with acquired tokens cached in
  `connection_tokens`.
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
make import-demo      # import the demo workflow's skills (required before running it)
make check-quick      # quick quality gate (daily)
make check            # full quality gate (before handoff)
make api-generate     # regenerate frontend API types
make skills-lock      # refresh the DB skill lock (global_settings skill_lock)
make install-hooks    # install pre-commit / pre-push gates
```

### Demo workflow (education_video_problems_generation)

The repository ships a minimal demo workflow: ten generic K-12 math
knowledge points under `examples/education-video-problems-generation/` are
fanned out one job each, then each job writes a teaching-video script, reviews
it, generates five exercises, reviews them, and finishes with a simulated
(no-network) publish. To run it:

```bash
make import-demo      # copy examples/skills/* into the local skill source root,
                      # git-init each and tag v1.0.0 (idempotent, never overwrites)
make skills-lock      # resolve the demo skill refs into the DB skill lock
```

`make import-demo` is a **required step**: the demo skill sources
(`~/.agents/skills/agent-legion/education-video-problems-generation/*`) are
created by it, and relocking or dispatching without it fails with a
"local skill repo not found" error that points back to the command. Then bind
a workspace to the `education_video_problems_generation` workflow and
configure the workspace's default agent model
(`default_agent_provider` / `default_agent_model` in workspace Settings) —
agent nodes still need a real LLM. See `examples/README.md` for the layout.

## Configuration

All runtime split yaml files are retired — `config/app.yaml`,
`config/workflow.yaml`, and `config/agent_legion.yaml` fail startup with
migration guidance when present. The effective configuration is composed from
code defaults, env overrides, and DB documents. Remaining tracked config
files:

| File | Owns |
|------|------|
| `server/app/workflows/builtin.py` (+ `builtin_demo.py`) | built-in workflow DAG definitions |

Skill sources and pinned refs are no longer tracked files: they live in the
DB `global_settings` documents `skill_sources` / `skill_lock`, managed through
the admin API (`GET/PUT /api/admin/skill-sources`,
`POST /api/admin/skill-sources/relock`) and the /admin/settings「Skill 源管理」
section; `make skills-lock` re-resolves the lock. A leftover
`config/skills.yaml` / `config/skills.lock` is imported into the DB once at
startup (with a warning) and never read again.

Bootstrap/security-level keys are env-only —
database URL comes from `AGENT_LEGION_DATABASE_URL`, the data root from
`AGENT_LEGION_DATA_DIR`, browser CORS origins from
`AGENT_LEGION_CORS_ALLOW_ORIGINS` / `AGENT_LEGION_CORS_ALLOW_CREDENTIALS`.
Instance-level tunables (cleanup/monitoring policy, lease/heartbeat/sweeper
timing, agent worker limits, `workflows.enabled`, the OpenClaw runtime block
`openclaw.*`) live in the DB
`global_settings` document `instance` and are edited through the admin API
`GET/PUT /api/admin/instance-settings`; they hydrate at startup and take
effect on restart. `AGENT_LEGION_OPENCLAW_CWD` stays as an env override that
outranks the DB value. Executor definitions (the retired `workflow.yaml`
`executors` section) live in the DB `versioned_entities` table, seeded from
the built-in factory catalog at startup and managed in Studio.

ASR (the retired `agent_legion.yaml` `asr:` section): the business parameters
`provider` (`auto` / `whisper` / `sensevoice`, default `auto`) and
`timeout_seconds` (default 900) are declared on the `transcribe_video`
capability `config_schema` and overridable per node/workspace in Studio; the
machine-local paths are env-only — `AGENT_LEGION_ASR_WHISPER_BINARY`,
`AGENT_LEGION_ASR_WHISPER_MODEL`, `AGENT_LEGION_ASR_WHISPER_VAD_MODEL`,
`AGENT_LEGION_ASR_SENSEVOICE_SCRIPT`, `AGENT_LEGION_ASR_SENSEVOICE_MODEL_DIR`.
Startup validates only env-provided paths (a typo fails fast); with no ASR
env configured the server starts fine and a missing binary surfaces as the
provider's `FileNotFoundError` at transcription time.

Secrets are never written to yaml: database URL comes from
`AGENT_LEGION_DATABASE_URL`, the vault master key from
`AGENT_LEGION_VAULT_MASTER_KEY[_FILE]`, the bootstrap admin password from
`AGENT_LEGION_BOOTSTRAP_ADMIN_PASSWORD`, and external service credentials
(e.g. CMS) live on instance-level connections (admin settings → 外部服务连接,
Fernet-encrypted in `instance_secrets`). Full reference:
[docs/architecture/backend.md](docs/architecture/backend.md).

## Agent Runtimes

Agent nodes execute through external skills — standalone git repositories
containing `SKILL.md`, an output contract, and a validator — typically checked
out under `~/.agents/skills/agent-legion/<workflow>/<capability>/` and pinned
by the DB `skill_lock` document (refresh with `make skills-lock`).

Two harness runtimes run them:

- **Pi CLI**: `npm install -g --ignore-scripts @earendil-works/pi-coding-agent`,
  then `pi` to authenticate and `./scripts/check-pi.sh` to verify. Enable per
  agent with `runtime: pi` in the Agent definition (Studio「Agent 管理」). Pi
  is an optional runtime; the production default is velites.
- **velites** (production default): a single static Rust binary built from
  `velites/` (`cargo build --release`), emitting the same event stream the host
  consumes. Enable per agent with `runtime: velites` in the Agent definition.
  `make native-prod-up` runs `scripts/ensure-velites.sh` before starting
  services: it fingerprints the `velites/` source tree (git tree hash) against
  a stamp next to the PATH binary and rebuilds + atomically reinstalls when
  stale, so a pulled-but-never-rebuilt binary cannot drift from the code.
  The retired `workflows.pi` yaml block (provider/model/timeout/flavor) no
  longer exists: execution provider/model/thinking come from the workspace
  Settings「Agent 默认配置」or per-node Studio overrides, and the manifest
  carries them under `execution.*`. See
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
- [docs/studio-agent-mcp.md](docs/studio-agent-mcp.md) — MCP server for
  external agents (token minting, client setup, permission boundary)
