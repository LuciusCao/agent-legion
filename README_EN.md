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

- Python 3.11+, Node 18+, PostgreSQL 17 (Homebrew: `brew install postgresql@17`)
- [`uv`](https://docs.astral.sh/uv/) for Python dependencies
- A Rust toolchain (`cargo`) to build **velites**, the sandboxed execution
  binary all node code runs through
- An LLM provider for agent nodes (any OpenAI-compatible endpoint works;
  the demo workflow needs one)

### 1. Clone and install

```bash
git clone https://github.com/LuciusCao/agent-legion.git
cd agent-legion
uv sync                                     # Python deps
createdb agent_legion
cp .env.example .env                        # then edit: set AGENT_LEGION_DATABASE_URL
cd frontend && npm install && cd ..
```

You also need to configure `AGENT_LEGION_S3_*` object storage (locally you
can run RustFS — see
[docs/materials-storage-deployment.md](docs/materials-storage-deployment.md)).
Without it the rest of the instance works, but the materials API degrades to
503 and demo material seeding is skipped.

### 2. One-time local setup

```bash
# Build the velites binary used to sandbox node code
./scripts/ensure-velites.sh --dest data/bin

# Local worker config (edit host_url to http://127.0.0.1:8001 and
# work_root to data/agent-worker — see the comments in the example file)
cp config/agent-worker.example.yaml config/agent-worker.yaml
```

Worker registration no longer uses a global token: after startup, issue a
scoped token per workspace in the Host Web UI (Settings → Worker Token) and
paste it into the "Workspace access" section of the Worker console
(`http://127.0.0.1:8789`) — tokens can be added at any time, no backend
restart needed (see [docs/agent-worker-deployment.md](docs/agent-worker-deployment.md)).

### 3. Start everything

```bash
make dev-up         # backend :8001, console :5174, worker :8789 — idempotent
make dev-status     # show component status and URLs
make dev-down       # stop everything
```

Open http://127.0.0.1:5174 — the first visit redirects to `/setup` to
create the admin user. Workers start with claiming disabled by design;
enable it in the worker console at http://127.0.0.1:8789.

### 4. Run the demo workflow

The repository ships a minimal demo workflow,
**`education_video_problems_generation`**: ten generic K-12 math knowledge
points under `examples/` are seeded as example materials into the demo
workspace (requires `AGENT_LEGION_S3_*` object storage; seeding is skipped
when it is not configured), each fanned out into one job — draft a teaching
video script, review it, generate five exercises, review them, then a
simulated (no-network) publish.

```bash
make import-demo      # install/lock skills and create+seed a demo workspace if absent
```

Then in the console:

1. Open the demo workspace printed by the command (reruns reuse it).
2. In workspace **Settings → Agent 默认配置**, set the provider/model your
   LLM endpoint serves.
3. Enable automatic scheduling for the workspace and claiming in the Worker
   console.
4. Submit items through the workspace console's **添加条目** (add) panel:
   upload the knowledge-point markdown as materials or select the seeded
   example materials (one material becomes one job), or paste external IDs
   bound to a configured connection.
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
| Understand how it works (architecture, config reference, runtimes) | [docs/architecture/](docs/architecture/README.md) |
| Contribute code | [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md) |
| Track changes | [CHANGELOG.md](CHANGELOG.md) |

## License

[MIT](LICENSE) © Lucius Cao
