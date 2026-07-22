# Agent Legion

Local processing console for educational videos. It queues knowledge videos and question explanation videos, downloads and transcribes them, runs the applicable openclaw content stages, previews partial/final artifacts, and packages completed JSON for handoff. (Formerly Video Hive.)

## Technology Stack

- **Backend**: Python 3.11+ (project currently runs on Python 3.13), FastAPI, Uvicorn, PostgreSQL, PyYAML, Requests
- **Frontend**: React 18, TypeScript 5.8, Vite, React Router v6, Zustand, `@mui/material` (MUI v6), `@xyflow/react` (React Flow), `dagre`, `katex`, `@tanstack/react-virtual`
- **Package Management**: `uv` for Python; `npm` for the frontend
- **Linting / Formatting**: Ruff (Python), ESLint + Prettier (TypeScript)
- **Static Type Check**: mypy (Python)
- **Testing**: pytest (with pytest-cov), Vitest (frontend)

## Current Shape

- Backend: FastAPI, PostgreSQL, background worker.
- Python tooling: `uv` for dependency/runtime management, `ruff` for lint/format, `mypy` for type checking.
- Frontend: Vite + TypeScript, ESLint + Prettier.
- Storage: PostgreSQL control plane plus `data/videos/{video_id}/`, `data/logs/`, `data/packages/`, `data/jobs/`.
- Video queue model: each item has `content_type`, `external_id`, optional `source_url`, and phase/status fields.
- Agent Legion workflow model: workspace-scoped DAG jobs with configurable workflow definitions (`config/workflows/`).

For the full directory tree, see [docs/architecture/project-structure.md](docs/architecture/project-structure.md).

## Setup

```bash
uv sync
createdb agent_legion
export VIDEO_HIVE_DATABASE_URL=postgresql://127.0.0.1:5432/agent_legion
cd frontend
npm install
```

When running inside a restricted sandbox, keep the uv cache in the project:

```bash
UV_CACHE_DIR=.uv-cache uv sync
```

> For architecture details, code style, and testing conventions, see [docs/architecture/](docs/architecture/).
> For Agent-specific operating rules, see [AGENTS.md](AGENTS.md).

Copy `.env.example` to `.env` and fill in secrets before running locally:

```bash
cp .env.example .env
# also copy the frontend dev proxy config if you work on the UI
cd frontend && cp .env.example .env
```

## Makefile Shortcuts

A `Makefile` is provided to simplify the most common commands. It automatically sets `UV_CACHE_DIR=.uv-cache`, so you can omit the prefix in restricted sandboxes.

```bash
make help              # 显示所有可用命令
make sync              # uv sync
make dev-backend       # 启动后端开发服务器
make dev-frontend      # 启动前端开发服务器
make check-quick       # 快速质量门
make check             # 完整质量门（提交前）
make check-ci          # CI 质量门
make audit             # 依赖漏洞审计（pip-audit + npm audit）
make skills-lock       # 刷新 config/skills.lock
make api-generate      # 重新生成前端 API 类型
make install-hooks     # 安装可选的预提交钩子
make architecture-ratchet   # 更新架构预算基线
make architecture-check     # 检查架构契约
make upload            # 上传审题信息包
make scan-comprehension    # 扫描审题信息 fingerprint 变化
make package-comprehension # 从 comprehension_info.json 生成 package.jsonl
make upload-workspace-package # 从 workspace zip 直接上传审题信息
```

## Configuration

Configuration is split by domain into files under `config/`:

- `config/app.yaml`: PostgreSQL URL, application paths, HTTP settings, worker concurrency, log/run-dir cleanup, and token-usage pricing.
- `config/video_hive.yaml`: ASR, CMS, resource providers, cleanup, and OpenClaw settings.
- `config/workflow.yaml`: workspace executors, workflow runtime, and Pi agent settings.
- `config/skills.yaml` / `config/skills.lock`: skill source declarations and resolved versions for Pi agent nodes.
- `config/agent-worker.example.yaml`: Worker Service 首次启动引导配置；启动后通过本机 `http://127.0.0.1:8787` 控制台或 `workerctl` 管理（控制台自动注入 control token，API 端点除 `/api/health` 外均需该 token）。

Edit `config/video_hive.yaml` for:

- `asr.provider`: `auto`, `whisper`, or `sensevoice`.
- `asr.whisper.binary`: local `whisper-cli`.
- `asr.whisper.model`: local whisper medium model.
- `asr.whisper.vad_model`: local VAD model for whisper.cpp.
- `asr.sensevoice.script`: SenseVoice SRT script.
- `asr.sensevoice.model_dir`: local `SenseVoiceSmall` model.
- `openclaw.command_template`: command argument list. Supported placeholders: `{prompt_text}`, `{video_id}`, `{timestamp}`.
- `openclaw.cwd`: working directory for openclaw.

In `auto` ASR mode, Agent Legion tries whisper.cpp first and falls back to SenseVoice if the SRT is missing, empty, unparsable, too short for the video, or obviously repetitive.

> See [docs/architecture/backend.md](docs/architecture/backend.md) for full configuration reference.
>
> You must also configure `config/skills.yaml` (and commit the generated `config/skills.lock`) for Agent Legion / Pi workflows to resolve skills. See the Pi section below.

## Video Types

Agent Legion treats knowledge videos and question explanation videos differently.

| Type | Required ID | URL | Pipeline |
| --- | --- | --- | --- |
| `knowledge` | knowledge code | optional at intake | download → transcribe → subtitle review → chapter generation → interaction generation → content review → assemble → package |
| `question` | question ID | optional at intake | download → transcribe → subtitle review → chapter generation → assemble → package |

If a knowledge code or question ID currently has no video URL, add it anyway with an empty URL. The record is stored with:

- `status`: `missing_url`
- `current_phase`: `waiting_for_url`

The worker skips `missing_url` records until a URL is supplied later.

Compatibility with the existing `~/CatPuru/projects/cms-extensions/engineering/llm_claude` fetch flow:

- knowledge rows: `tag_code -> knowledge_code`, `url -> source_url`
- question rows: `question_uuid -> question_id`, `url -> source_url`

Question explanation videos do not produce interactive nodes. Their assembled `metadata.json` keeps `nodes: []`, and the deterministic assemble path can create an empty `interactions.json`.

## Workflows

In addition to the video pipelines above, Agent Legion runs workspace-scoped DAG workflows defined in `config/workflows/`.

### `question_comprehension_info`

The `question_comprehension_info` workflow generates structured comprehension metadata for math word problems. It does not download or transcode video; it reads question data from CMS and produces `comprehension_info.json`.

Intake modes (configured per workspace):

- `batch_by_knowledge`: intake field `knowledge_codes`, fetch questions by knowledge code.
- `batch_by_ids`: intake field `question_ids`, fetch questions by UUID list.

Node DAG:

1. `fetch_questions`: fetch raw question JSON from CMS.
2. `clean_and_parse`: parse and clean question content; produce lean and full variants.
3. `classify_comprehension_eligibility`: decide whether the questions are eligible for comprehension analysis.
   - If `eligible = false`: terminal node `finalize_non_uploadable`.
   - If `eligible = true`: continue to `generate_key_info`.
4. `generate_key_info` → `review_key_info`: generate and review key information a student must read/infer.
5. `review_key_info` → `generate_possible_errors` → `review_possible_errors`: generate and review plausible comprehension errors.
6. `assess_comprehension_difficulty`: score comprehension difficulty independently from solution difficulty.
7. `assemble_comprehension_info`: merge reviewed artifacts into `comprehension_info.json` and `manifest.json` (uploadable terminal).

Agent nodes in this workflow execute through the Pi agent runner using external skills declared in `config/skills.yaml`.

## Run

Backend with automatic worker:

```bash
UV_CACHE_DIR=.uv-cache uv run uvicorn server.app.main:app --reload --reload-dir server --timeout-graceful-shutdown 3 --host 127.0.0.1 --port 8000
```

The backend has no authentication layer; always bind it to `127.0.0.1` and never expose it with `--host 0.0.0.0`.

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
UV_CACHE_DIR=.uv-cache uv run uvicorn server.app.main:app --reload --reload-dir server --timeout-graceful-shutdown 3 --port 8001
cd frontend
cp .env.example .env
printf 'VITE_API_TARGET=http://127.0.0.1:8001\n' > .env
npm run dev -- --port 5174
```

Use different ports and PostgreSQL databases or schemas for each additional worktree. Keep each
worktree's `data/` directory separate so logs, videos, packages, and jobs do not overlap.

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

Runs two buffered parallel rounds: backend/frontend static checks first, then pytest/Vitest. The
backend side covers Ruff lint + format check, Python tests with coverage (`fail_under = 85`), mypy, architecture invariant checks
(`scripts/check_invariants.py`), shared skill asset checks (`scripts/check-skills-shared.py`), the
whole-repository architecture contract check (`scripts/check_architecture.py`), and spec health
(`scripts/verify_specs.py --check`). The frontend lane covers generated API drift, Prettier,
ESLint, typecheck, and Vitest without coverage. Vitest uses its thread pool to reduce per-file
process startup overhead while retaining file isolation. Repository-wide architecture assertions are
excluded from the parallel pytest run because the backend lane executes the authoritative scan
once after pytest.

`mypy` runs with unreachable-code warnings enabled. When it flags code behind
dynamic JSON, database, or framework boundaries, first check whether the type
annotation is too narrow before deleting the branch.

To check frontend coverage explicitly, run `npm run test:coverage` in `frontend/`, or run the full gate.

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

Runs the parallel quick gate with frontend coverage enabled, then runs full-gate backend evidence
and the frontend production bundle in parallel. Frontend tests and TypeScript typechecking are not
repeated: coverage replaces the quick Vitest invocation, while the production extension only runs
`vite build`. The non-blocking dependency vulnerability audit remains available through
`scripts/check-deps-audit.sh` and `make audit`.

Dead-code sweeps with tools such as Vulture are manual review aids, not daily
gate failures. Treat framework declarations, Pydantic fields, FastAPI route
parameters, and side-effect-only registration code as likely false positives
unless a cross-reference search confirms they are unused.

Run the full test suite:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest -q
```

With coverage report:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest -q --cov=server --cov-report=term-missing
```

Install the versioned local quality-gate hooks:

```bash
make install-hooks
```

The installer places small dispatchers in the Git common hooks directory. Each dispatcher executes
the versioned `.githooks/` implementation from the current worktree when that branch contains it;
older worktrees without `.githooks/` remain unaffected. Successful gate evidence is shared:

- pre-commit runs `scripts/check-fast.sh` for lint, formatting, and type feedback;
- pre-push runs `scripts/check-quick.sh` for feature branches;
- pushes to `develop`, `main`, `master`, `release/*`, or tags run `scripts/check.sh`;
- successful pre-push evidence is cached by commit SHA in the Git common directory, so all
  worktrees can reuse an unchanged result;
- pre-push refuses a dirty worktree so the verified commit is exactly the commit being pushed.

Set `VIDEO_HIVE_LOCAL_GATE_FORCE=1` to force a fresh pre-push verification for an unchanged
commit. See [Local Quality Gates](docs/architecture/local-quality-gates.md) for the GitLab setup
and operating policy used when no CI runner is available.

> See [docs/architecture/](docs/architecture/) for architecture details, code style conventions, and security notes.

## Pipeline

Common phases:

1. `download`: saves `{video_id}.mp4`.
2. `transcribe`: writes `subtitles.srt` and `transcription.json`.
3. `subtitle_review`: real openclaw command writes reviewed subtitles and report.
4. `chapter_generate`: real openclaw command writes chapters.
5. `assemble`: writes `metadata.json` and `report.md`.

Knowledge-only phases:

6. `interaction_generate`: real openclaw command writes interactions.
7. `content_review`: real openclaw command writes checklist and review result.

Final phase:

8. `package`: creates a zip with per-video JSON plus `manifest.json`. The manifest includes `content_type`, `external_id`, `knowledge_code`, and `question_id`.

Question explanation videos skip `interaction_generate` and `content_review`.

## Pi Agent Runner

Agent Legion can execute `question_comprehension_info` workflow agent nodes through the Pi CLI (`@earendil-works/pi-coding-agent`).

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
    provider: deepseek
    model: your-model-b
    thinking: low
    timeout_seconds: 900
    environment:
      PI_SKIP_VERSION_CHECK: "1"
      PI_TELEMETRY: "0"
```

- `provider` / `model`: Pi provider and model. The defaults above are shipped in `config/workflow.yaml`; override them if your Pi setup uses a different default.
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

Rerunning a node deletes that node's and all downstream nodes' declared outputs and `runs/` history. Ancestor node runs are preserved.

### Authentication

Do not pass API keys on the command line. Pi inherits authentication from its environment or existing login store. Set provider credentials through Pi's standard environment variables if needed.

## SQLite to PostgreSQL migration

The application no longer has a SQLite runtime mode. For an existing installation, stop all
writers and import the final SQLite database into a fresh PostgreSQL database once:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/import-sqlite-to-postgres.py \
  data/video_hive.sqlite \
  postgresql://127.0.0.1:5432/agent_legion
```

The target must be empty unless `--truncate-target` is passed explicitly. Keep the SQLite file as
a rollback snapshot until row counts and application smoke tests have been verified. See
[the PostgreSQL runbook](docs/postgresql-runbook.md).

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

## Large-scale agent stress testing

Run backend synthetic event stress:

```bash
uv run python scripts/stress/simulate_agents.py --workspace ws-stress --agents 100 --jobs 5000 --event-rate 500 --duration 600
```

Run workspace UI stress:

```bash
cd frontend && STRESS_WORKSPACE_ID=ws-stress STRESS_DURATION_MS=300000 npm run stress:workspace
```

Run report generation:

```bash
uv run python scripts/stress/run_e2e_stress.py --agents 100 --jobs 5000 --duration 600 --browser chromium
```
