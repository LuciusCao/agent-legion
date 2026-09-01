# Agent Legion

*[中文版 README](README.md)*

Agent Legion is a self-hosted console that turns AI agents into a managed
production line for content workflows. You describe a workflow as a DAG of
business **capabilities** — "intake a batch", "write a script", "review it",
"generate practice questions" — and Agent Legion schedules every node across
your machines, with a live console to watch progress, rerun failures, and
collect the packaged results.

It is built for running LLM agents not as one-off chats, but as a
repeatable, auditable production process.

## What you get

- **Workflows your business can read.** Nodes declare what they do
  (a capability and its input/output artifacts), never how to run it.
  Author and publish workflows visually in the built-in Studio; every
  workspace owns its DAG and its revision history.
- **Batch in, results out.** Submit a batch of work items through one API
  call; each item becomes a job that flows through the DAG. Rerun a single
  node, run to a target node, or resume from a pause — downstream staleness
  is tracked for you. Beyond single-file materials and external references,
  items can also be **bundles**: an entire folder submitted as one item
  (manifest-referenced).
- **A live operations console.** React SPA with a real-time DAG view, SSE
  dashboard events, WebSocket agent status, run logs, artifacts,
  token-usage statistics, and failure-category batch rerun.
- **Scale out by adding machines.** Remote Agent Workers register over
  HTTP, claim executions, and upload artifacts. Capacity is leased and
  enforced per pool, so a flood of cheap code tasks never starves your
  agent runs.
- **Reproducible and auditable.** External skills are plain git repos
  pinned to a locked commit; every node execution leaves a full trace
  (prompt, event stream, stderr) you can inspect after the fact.
- **Secrets handled properly.** Workspace and instance credentials are
  Fernet-encrypted in a vault; configs and snapshots only ever carry
  references, never plaintext.
- **Multi-user by default.** Cookie sessions with CSRF guard, admin user
  management, and per-workspace editor/viewer membership.

## Quick Start

### Prerequisites

- macOS with Homebrew: `make install` auto-detects and installs missing
  prerequisites (Python 3.11+, Node 18+, PostgreSQL 17,
  [`uv`](https://docs.astral.sh/uv/), a Rust toolchain, Docker). On other
  platforms install those manually first; `make install` still checks each
  one and prints guidance.
- An LLM provider for agent nodes (any OpenAI-compatible endpoint works;
  the demo workflow needs one).

### 1. Clone and install

```bash
git clone https://github.com/LuciusCao/agent-legion.git
cd agent-legion
make install    # install prerequisites, uv sync, create the agent_legion_dev
                # database, generate .env (with random local-RustFS
                # credentials), generate the vault master key, build velites,
                # install frontend deps, seed the worker config — idempotent,
                # safe to re-run
```

The development database uses the derived name `agent_legion_dev`, never the
bare `agent_legion`: the bare name is the shared/prod database, and
`init_db` refuses to migrate it without `AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA=1`
(the shared-database schema guard).

Object storage defaults to local **RustFS** (`make dev-up` starts the
container and creates the bucket automatically; credentials are generated
into `.env` by `make install`), so it works out of the box. To switch to a
cloud S3 (AWS or any compatible service), change `AGENT_LEGION_S3_ENDPOINT` /
credentials / `AGENT_LEGION_S3_BUCKET` in `.env` — the local RustFS is then
skipped automatically (see
[docs/materials-storage-deployment.md](docs/materials-storage-deployment.md)).

When Docker is unavailable (not installed or not running), `make dev-up`
skips the local RustFS: demo material seeding is skipped, materials-related
APIs degrade to 503, everything else keeps working. Once Docker is up,
re-running `make dev-up` restores storage (the RustFS container + bucket);
if demo material seeding was skipped in the meantime (you had already run
`make import-demo`), run `make import-demo` again (idempotent) to seed the
materials — `make dev-up` itself never re-seeds them.

### 2. Start everything

```bash
make dev-up         # local RustFS + backend :8001 + console :5174 + worker :8789 — idempotent
make dev-status     # show component status and URLs
make dev-down       # stop everything
```

Open http://127.0.0.1:5174 — the first visit redirects to `/setup` to
create the admin user. Workers start with claiming disabled by design;
enable it in the worker console at http://127.0.0.1:8789.

Worker registration no longer uses a global token: after startup, issue a
scoped token per workspace in the Host Web UI (workspace Settings →
「Agent 与 Worker」) and paste it into the "Workspace access" section of the
Worker console (`http://127.0.0.1:8789`) — tokens can be added at any time,
no backend restart needed (see
[docs/agent-worker-deployment.md](docs/agent-worker-deployment.md)).

### 3. Run the demo workflow

The repository ships a minimal demo workflow,
**`education_video_problems_generation`**: ten generic K-12 math knowledge
points under `examples/` are seeded as example materials into the demo
workspace, each fanned out into one job — draft a teaching video script,
review it, generate five exercises, review them, then a simulated (no-network)
publish.

```bash
make import-demo      # install/lock skills and create+seed a demo workspace if absent
```

Then in the console:

1. Open the demo workspace printed by the command (reruns reuse it).
2. Configure agent execution in Studio: open the workflow and fill the
   top-level `execution:` block with the provider/model your LLM endpoint
   serves (one place covers every agent node; individual nodes can override
   via `execution.*`; the input lists the provider/model options reported by
   online Workers for that node's Agent runtime, and free text works too).
3. Enable automatic scheduling for the workspace and claiming in the Worker
   console.
4. Submit a batch: in the workspace's **添加条目** (add) dialog, upload the
   knowledge-point markdown, or select the seeded example materials in the
   panel, then confirm to create the run — one material becomes one job.
   (The "paste ID" panel is for **ref items**: configure an external service
   connection in admin first, then paste an external ID under it.)
5. Watch the DAG light up in real time, and inspect each node's trace and
   artifacts when it finishes.

### Where to go next

- **Author your own workflow** in Studio (draft → publish) and attach your
  own skills — see `examples/README.md` for how the demo is wired.
- **Add more machines** as workers:
  [docs/agent-worker-deployment.md](docs/agent-worker-deployment.md).
- **Deploy for production** (Docker stacks, PostgreSQL):
  [docs/architecture/deployment.md](docs/architecture/deployment.md) and
  [docs/postgresql-runbook.md](docs/postgresql-runbook.md).

## Documentation

| I want to… | Read |
|------------|------|
| Get it running / run the demo | this file + `examples/README.md` |
| Operate it (deploy, workers, remote execution) | [docs/](docs/README.md) — deployment, worker, and runbook docs |
| Material storage (RustFS/S3) | [docs/materials-storage-deployment.md](docs/materials-storage-deployment.md) |
| Understand how it works (architecture, config reference, runtimes) | [docs/architecture/](docs/architecture/README.md) |
| Contribute code | [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md) |
| Track changes | [CHANGELOG.md](CHANGELOG.md) |

## License

[MIT](LICENSE) © Lucius Cao
