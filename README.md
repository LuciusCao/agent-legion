# Agent Legion

Local processing console for educational videos. It queues knowledge videos and question explanation videos, downloads and transcribes them, runs the applicable openclaw content stages, previews partial/final artifacts, and packages completed JSON for handoff. (Formerly Video Hive.)

## Current Shape

- Backend: FastAPI, SQLite, background worker.
- Python tooling: `uv` for dependency/runtime management, `ruff` for lint/format, `mypy` for type checking.
- Frontend: Vite + TypeScript, ESLint + Prettier.
- Storage: `data/video_hive.sqlite`, `data/videos/{video_id}/`, `data/logs/`, `data/packages/`, `data/jobs/`.
- Video queue model: each item has `content_type`, `external_id`, optional `source_url`, and phase/status fields.
- Agent Legion workflow model: workspace-scoped DAG jobs with configurable workflow definitions (`config/workflows/`).

## Setup

```bash
uv sync
cd frontend
npm install
```

When running inside a restricted sandbox, keep the uv cache in the project:

```bash
UV_CACHE_DIR=.uv-cache uv sync
```

> For architecture details, code style, and testing conventions, see [AGENTS.md](AGENTS.md).

## Makefile Shortcuts

A `Makefile` is provided to simplify the most common commands. It automatically sets `UV_CACHE_DIR=.uv-cache`, so you can omit the prefix in restricted sandboxes.

```bash
make help              # 显示所有可用命令
make sync              # uv sync
make dev-backend       # 启动后端开发服务器
make dev-frontend      # 启动前端开发服务器
make check-quick       # 快速质量门
make check             # 完整质量门（提交前）
make skills-lock       # 刷新 config/skills.lock
make api-generate      # 重新生成前端 API 类型
```

## Configuration

Configuration is split by domain into three files under `config/`:

- `config/app.yaml`: application paths, HTTP settings, and worker concurrency.
- `config/video_hive.yaml`: ASR, CMS, resource providers, cleanup, and OpenClaw settings.
- `config/workflow.yaml`: workspace executors and workflow runtime settings.

Edit `config/video_hive.yaml` for:

- `asr.provider`: `auto`, `whisper`, or `sensevoice`.
- `asr.whisper.binary`: local `whisper-cli`.
- `asr.whisper.model`: local whisper medium model.
- `asr.sensevoice.script`: SenseVoice SRT script.
- `asr.sensevoice.model_dir`: local `SenseVoiceSmall` model.
- `openclaw.command_template`: command argument list. Supported placeholders: `{prompt_file}`, `{video_id}`, `{video_dir}`.
- `openclaw.cwd`: working directory for openclaw.

In `auto` ASR mode, Video Hive tries whisper.cpp first and falls back to SenseVoice if the SRT is missing, empty, unparsable, too short for the video, or obviously repetitive.

> See [AGENTS.md](AGENTS.md) for full configuration reference.
>
> You must also configure `config/skills.yaml` (and commit the generated `config/skills.lock`) for Agent Legion / Pi workflows to resolve skills. See the Pi section below.

## Video Types

Video Hive treats knowledge videos and question explanation videos differently.

| Type | Required ID | URL | Pipeline |
| --- | --- | --- | --- |
| `knowledge` | knowledge code | optional at intake | download → transcribe → subtitle review → chapter generation → interaction generation → content review → assemble |
| `question` | question ID | optional at intake | download → transcribe → subtitle review → chapter generation → assemble |

If a knowledge code or question ID currently has no video URL, add it anyway with an empty URL. The record is stored with:

- `status`: `missing_url`
- `current_phase`: `waiting_for_url`

The worker skips `missing_url` records until a URL is supplied later.

Compatibility with the existing `~/CatPuru/projects/cms-extensions/engineering/llm_claude` fetch flow:

- knowledge rows: `tag_code -> knowledge_code`, `url -> source_url`
- question rows: `question_uuid -> question_id`, `url -> source_url`

Question explanation videos do not produce interactive nodes. Their assembled `metadata.json` keeps `nodes: []`, and the deterministic assemble path can create an empty `interactions.json`.

## Run

Backend with automatic worker:

```bash
UV_CACHE_DIR=.uv-cache uv run uvicorn server.app.main:app --reload --reload-dir server --port 8000
```

Frontend during development:

```bash
cd frontend
npm run dev
```

Open the Vite URL shown by npm.

Generated frontend transport types are committed to `frontend/src/generated/api.ts`. After changing
any Pydantic response model, regenerate them before committing:

```bash
cd frontend
npm run api:generate
```

The frontend calls the backend API on the same origin in production; during development, Vite proxies `/api` to `VITE_API_TARGET` from `frontend/.env` and defaults to `http://127.0.0.1:8000`.

For multiple coding agents working in separate git worktrees, give each worktree its own backend/frontend ports and local frontend env:

```bash
# worktree A
UV_CACHE_DIR=.uv-cache uv run uvicorn server.app.main:app --reload --reload-dir server --port 8001
cd frontend
cp .env.example .env
printf 'VITE_API_TARGET=http://127.0.0.1:8001\n' > .env
npm run dev -- --port 5174
```

Use different ports for each additional worktree, and keep each worktree's default `data/` directory separate so SQLite state, logs, videos, packages, and jobs do not overlap.

Production-style frontend build:

```bash
cd frontend
npm run build
```

After `frontend/dist` exists, the FastAPI backend serves it from `http://127.0.0.1:8000`.

## Quality Gates

Quick gate (for daily development):

```bash
./scripts/check-quick.sh
```

Runs Ruff lint + format check, Python tests with coverage (`fail_under = 85`), mypy, architecture
contract checks (`scripts/check_architecture.py`), generated API type drift check
(`npm run api:check`), frontend Prettier + ESLint + typecheck + Vitest, and the spec health check
(`scripts/verify_specs.py --check`).

Architecture source budgets are governed by `config/architecture/architecture-budget-policy.yaml`
(human-maintained policy) and `config/architecture/architecture-budgets.json` (machine-maintained
baseline). Update the baseline and verify it with:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/ratchet_architecture_budgets.py
UV_CACHE_DIR=.uv-cache uv run python scripts/check_architecture.py
```

The ratchet script refuses to raise ceilings; over-budget files must be split or reverted.

Full gate (before committing or handing off):

```bash
./scripts/check.sh
```

Runs the quick gate plus the frontend production build.

Install the optional pre-commit hook:

```bash
./scripts/install-git-hooks.sh
```

> See [AGENTS.md](AGENTS.md) for architecture details, code style conventions, and security notes.

## Pipeline

Common phases:

1. `download`: saves `{video_id}.mp4`.
2. `transcribe`: writes `subtitles.srt` and `transcription.json`.
3. `subtitle_review`: real openclaw command writes reviewed subtitles and report.
4. `chapter_generate`: real openclaw command writes chapters.

Knowledge-only phases:

5. `interaction_generate`: real openclaw command writes interactions.
6. `content_review`: real openclaw command writes checklist and review result.

Final phase:

7. `assemble`: writes `metadata.json` and `report.md`.

The package endpoint creates a zip with per-video JSON plus `manifest.json`. The manifest includes `content_type`, `external_id`, `knowledge_code`, and `question_id`.

## Pi Agent Runner

Video Hive can execute `question_comprehension_info` workflow agent nodes through the Pi CLI (`@earendil-works/pi-coding-agent`).

### Installation

```bash
npm install -g --ignore-scripts @earendil-works/pi-coding-agent
pi
# Follow the login prompt to authenticate
./scripts/check-pi.sh
```

### Configuration

Pi CLI settings live in `config/workflow.yaml` under `workflows.pi`:

```yaml
workflows:
  enabled: true
  pi:
    binary: pi
    provider: ""        # empty = use Pi default
    model: ""           # empty = use Pi default
    thinking: low
    timeout_seconds: 600
    environment:
      PI_SKIP_VERSION_CHECK: "1"
      PI_TELEMETRY: "0"
```

- `provider` / `model`: leave empty to use Pi's configured default.
- `timeout_seconds`: per-node timeout. Pi is terminated if it exceeds this.
- `environment`: merged into Pi's subprocess environment.

### External Skills

Each agent node in `question_comprehension_info` (and other workflows) maps to a skill in a **standalone git repository** managed by `SkillManager`. Local copies typically live under:

```text
~/.agents/skills/agent-legion/<workflow>/<capability>/
```

For example:

```text
~/.agents/skills/agent-legion/question_comprehension_info/generate_key_info/
~/.agents/skills/agent-legion/question_comprehension_info/review_possible_errors/
```

Skill sources are declared in `config/skills.yaml` and pinned by `config/skills.lock`. To migrate or re-migrate skills from the current source tree, run:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/migrate-skills-to-external-repos.py
```

Every skill repo must contain:

- `SKILL.md` — execution workflow and I/O contract
- `references/output-contract.md` — field-level artifact specification
- `scripts/validate_output.py` — node-specific validator

Pi loads **only** the declared skill. Automatic skill discovery, extensions, prompt templates, and context files are disabled.

### Run Directory Layout

Every Pi execution creates a fresh trace under `{job_dir}/runs/{node_key}/{run_token}/`:

```
runs/extract_keywords/550e8400-e29b-41d4-a716-446655440000/
  prompt.md          # orchestration prompt passed to Pi
  events.jsonl       # Pi JSON event stream (stdout)
  stderr.log         # Pi diagnostic output (stderr)
  run.json           # metadata: command, start/end time, exit code, error
  session/           # Pi session directory
```

Previous runs are preserved. Rerunning a node deletes that node's and all downstream nodes' declared outputs, but never touches `runs/` history.

### Authentication

Do not pass API keys on the command line. Pi inherits authentication from its environment or existing login store. Set provider credentials through Pi's standard environment variables if needed.

## Phase 5 Workspace Executor Migration

If you are upgrading from a pre-Phase-5 database that still contains Workspace Agent
assignments or `pipeline_config_json`, run the one-time finalizer before starting the server:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/finalize-workspace-executor-migration.py --check
UV_CACHE_DIR=.uv-cache uv run python scripts/finalize-workspace-executor-migration.py --apply
```

- `--check` is read-only and prints a JSON report. An empty `issues` list means finalization can
  proceed safely.
- `--apply` creates a timestamped SQLite backup beside `data/video_hive.sqlite` and then migrates
  legacy Agent/Workflow settings into Executor allocations, bindings, and local Node limits.

If the report lists unknown legacy Agent IDs, either configure an equivalent Executor in
`config/workflow.yaml` or manually remediate the `workspace_agent_assignments` rows before
retrying. The server refuses to start until `--check` reports zero issues.

## API Notes

Add a knowledge video with URL:

```json
{
  "items": [
    {
      "content_type": "knowledge",
      "external_id": "K001",
      "title": "知识点标题",
      "url": "https://example.com/k001.mp4"
    }
  ]
}
```

Add a question explanation record before its URL is available:

```json
{
  "items": [
    {
      "content_type": "question",
      "external_id": "Q001",
      "title": "题目解析标题",
      "url": ""
    }
  ]
}
```

Useful endpoints (video pipeline):

- `POST /api/videos`
- `GET /api/videos`
- `GET /api/videos/{video_id}`
- `DELETE /api/videos/{video_id}` deletes the queue record and that video's local storage directory.
- `POST /api/videos/{video_id}/rerun`
- `GET /api/videos/{video_id}/artifacts`
- `GET /api/videos/{video_id}/logs`
- `POST /api/worker/tick` processes one local non-agent phase; agent phases are handled by the background worker runner pool.
- `POST /api/package`

Agent Legion workflow (workspace / job) endpoints:

- `GET /api/workflows/{workflow_key}` — workflow definition metadata (includes `intake.modes` without `task_entity` / `resolver`)
- `GET /api/workspaces` — list workspaces
- `POST /api/workspaces` — create workspace (supports `default_entity` and `intake_config`)
- `GET /api/workspaces/{workspace_id}` — get workspace (returns `default_entity` and `intake_config`)
- `PATCH /api/workspaces/{workspace_id}` — update workspace (supports `default_entity` and `intake_config`)
- `POST /api/workspaces/{workspace_id}/job-batches` — create a batch of jobs (supports `entity`; defaults to workspace `default_entity`)
- `GET /api/workspaces/{workspace_id}/jobs` — list jobs in workspace
- `GET /api/jobs/{job_id}` — job detail with nodes, runs, artifacts
- `GET /api/jobs/{job_id}/artifacts/{artifact_name}` — read job artifact
- `GET /api/jobs/{job_id}/runs/{run_id}/log` — safe run log content
- `POST /api/jobs/{job_id}/nodes/{node_key}/rerun` — rerun a node and mark downstream nodes stale
- `POST /api/jobs/{job_id}/run-to` — run only the ancestor closure up to a target node
- `POST /api/jobs/{job_id}/continue` — continue a paused job after a run-to target was reached
- `DELETE /api/jobs/{job_id}` — delete job records, storage, and logs
- `POST /api/workspaces/{workspace_id}/jobs/package` — package completed jobs

Generic Workspace Job code follows the boundary: UI reads persisted Node state, mutations call
services, and the scheduler claims Nodes through Executor leases. See [AGENTS.md](AGENTS.md) for
Phase 6 architecture rules and wrong examples.

