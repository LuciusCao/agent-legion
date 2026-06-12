# Video Hive — Agent Guide

## Project Overview

Video Hive is a local processing console for educational videos. It maintains a queue of video links, distinguishes between **knowledge videos** and **question explanation videos**, downloads and transcribes them, runs the applicable external agent stages through `openclaw`, previews partial and final artifacts in a web UI, and packages completed results into a ZIP with per-video JSON and a manifest.

The project is a monorepo with a Python FastAPI backend and a React + TypeScript frontend built with Vite.

## Technology Stack

- **Backend**: Python 3.11+, FastAPI, Uvicorn, SQLite, PyYAML, Requests
- **Frontend**: React 18, TypeScript 5.8, Vite, React Router v6, Zustand, `@material/web` (Material 3 Web Components)
- **Package Management**: `uv` for Python; `npm` for the frontend
- **Linting / Formatting**: Ruff (Python), ESLint + Prettier (TypeScript)
- **Static Type Check**: mypy (Python)
- **Testing**: pytest (with pytest-cov), Vitest (frontend)

## Project Structure

```
video-hive/
├── pyproject.toml              # Python project metadata, dependencies, tool config
├── uv.lock                     # Locked Python dependency tree
├── config/
│   └── pipeline.yaml           # Runtime configuration (ASR providers, openclaw)
├── server/
│   ├── app/
│   │   ├── main.py             # FastAPI app factory + lifespan worker threads
│   │   ├── routes/             # REST API routes (videos, agents, worker, artifacts, packages, jobs, workspaces)
│   │   ├── db/                 # SQLite database wrapper (schema, queries, notifications)
│   │   ├── cms/                # CMS API integration (auth, client, knowledge, question)
│   │   ├── jobs/               # Job queries for Agent Legion pipeline
│   │   ├── services/           # Business logic services
│   │   │   ├── intake.py       # Video intake (add, URL resolution)
│   │   │   ├── video_actions.py # Batch rerun, delete, package selection
│   │   │   ├── manual_run.py   # Manual phase run orchestration
│   │   │   └── interaction_stats.py # Interaction statistics aggregation
│   │   ├── settings.py         # Settings loader from YAML
│   │   ├── worker.py           # Background worker loop + per-video phase processing
│   │   ├── worker_control.py   # Worker pause/resume control
│   │   ├── worker_thread.py    # Background worker thread lifecycle
│   │   ├── worker_scheduler.py # Worker scheduling logic
│   │   ├── pipeline_worker_thread.py # Agent Legion pipeline worker thread
│   │   ├── events.py           # SSE event broadcaster
│   │   ├── agents.py           # OpenClaw agent discovery and status tracking
│   │   ├── records.py          # TypedDict type definitions for DB records
│   │   ├── pipeline/           # Video pipeline stage implementations
│   │   │   ├── common.py       # URL-to-id parsing, SRT parse/format helpers
│   │   │   ├── phases.py       # Phase list and agent-phase definitions
│   │   │   ├── download.py     # HTTP video downloader
│   │   │   ├── transcribe.py   # ASR providers (whisper.cpp / SenseVoice) + fallback logic
│   │   │   ├── openclaw.py     # OpenClaw command runner
│   │   │   ├── assemble.py     # Final metadata.json assembly
│   │   │   ├── artifacts.py    # Artifact cleanup on rerun
│   │   │   ├── reader.py       # Artifact reader for the API
│   │   │   ├── package.py      # ZIP packaging of completed videos
│   │   │   ├── upload_params.py # Assemble upload_params.json in llm_claude format
│   │   │   ├── fetch_url.py    # CMS API integration for knowledge/question lookups
│   │   │   ├── runners.py      # OpenClaw runner pool
│   │   │   ├── recovery.py     # Interrupted video recovery on startup
│   │   │   ├── validators.py   # Input validators
│   │   │   └── references/     # Markdown prompt references for openclaw phases
│   │   │       ├── phase-03-subtitle-review.md
│   │   │       ├── phase-04-chapter-generate.md
│   │   │       ├── phase-05-interaction-generate.md
│   │   │       └── phase-06-content-review.md
│   │   └── pipelines/          # Agent Legion DAG pipeline definitions
│   │       ├── definition.py   # Pipeline definition loader
│   │       ├── executor.py     # Pipeline node executor
│   │       ├── scheduler.py    # DAG scheduling and downstream node resolution
│   │       ├── registry.py     # Pipeline definition registry by key
│   │       ├── reading_analysis.py # Reading analysis local node handlers
│   │       ├── question_content.py # Question content pipeline presets
│   │       ├── pi_runner.py    # Pi CLI runner for agent nodes
│   │       ├── artifacts.py    # Artifact validation and rerun cleanup
│   │       ├── skills.py       # Repository-owned skill resolution
│   │       ├── resources.py    # Resource provider bindings
│   │       └── skills/         # Repository-owned Pi skills (reading_analysis/*)
├── frontend/
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx            # React entry point
│       ├── App.tsx             # Router shell (React Router)
│       ├── api.ts              # Thin fetch wrapper
│       ├── types.ts            # Shared TypeScript types
│       ├── labels.ts           # UI labels & phase lists
│       ├── helpers.ts          # Pure utility functions
│       ├── theme.ts            # MD3 CSS custom properties
│       ├── styles.css          # Global styles
│       ├── pages/              # Route-level pages
│       │   ├── ListPage.tsx
│       │   ├── DetailPage.tsx
│       │   └── WorkspacesPage.tsx
│       ├── components/         # Reusable UI components
│       │   ├── AddDialog.tsx
│       │   ├── AgentPanel.tsx
│       │   ├── BatchDeleteDialog.tsx
│       │   ├── BatchRerunDialog.tsx
│       │   ├── BatchToolbar.tsx
│       │   ├── ChapterPanel.tsx
│       │   ├── ChapterStrip.tsx
│       │   ├── DeleteDialog.tsx
│       │   ├── InteractionOverlay.tsx
│       │   ├── InteractionReviewBadge.tsx
│       │   ├── MetadataPanel.tsx
│       │   ├── NodePanel.tsx
│       │   ├── PackageHistoryDialog.tsx
│       │   ├── PackageToolbar.tsx
│       │   ├── PhaseRunsPanel.tsx
│       │   ├── PhaseStepper.tsx
│       │   ├── RerunDialog.tsx
│       │   ├── RunToDialog.tsx
│       │   ├── StatCards.tsx
│       │   ├── SubtitlePanel.tsx
│       │   ├── TimelineStrip.tsx
│       │   ├── Toast.tsx
│       │   ├── TranscriptionDetails.tsx
│       │   ├── VideoList.tsx
│       │   └── VideoPlayer.tsx
│       ├── hooks/              # React custom hooks
│       │   ├── useDebouncedCallback.ts
│       │   ├── useDetailPage.ts
│       │   ├── usePhaseRunsTimeline.ts
│       │   ├── useVideoEvents.ts
│       │   └── useVideoPhaseEvents.ts
│       └── stores/             # Zustand state stores
│           ├── artifactStore.ts
│           ├── detailStore.ts
│           ├── interactionStore.ts
│           ├── packageStore.ts
│           ├── uiStore.ts
│           └── videoStore.ts
├── tests/
│   ├── test_core.py            # Pipeline utility unit tests
│   ├── test_api.py             # FastAPI endpoint tests with TestClient
│   ├── test_agents.py          # AgentStatusManager unit tests
│   ├── test_db.py              # Database tests
│   ├── test_fetch_url.py       # CMS token/video lookup tests
│   ├── test_interaction_stats.py
│   ├── test_jobs.py            # Job model tests
│   ├── test_jobs_api.py        # Jobs API endpoint tests
│   ├── test_openclaw_sessions.py
│   ├── test_security.py        # Security tests
│   ├── test_services.py        # Service layer unit tests
│   ├── test_settings.py
│   ├── test_video_actions.py
│   ├── test_worker.py          # Worker-phase integration tests
│   ├── test_worker_scheduler.py
│   ├── test_worker_thread.py
│   ├── test_pipeline_*.py      # Pipeline stage unit tests (download, transcribe, assemble, etc.)
│   ├── test_pi_runner.py       # Pi agent runner tests
│   └── ...
└── data/                       # Runtime data (gitignored)
    ├── video_hive.sqlite
    ├── videos/
    ├── logs/
    ├── packages/
    └── jobs/
```

## 文档体系

- `README.md` — 项目概览、快速上手、安装与运行命令
- `AGENTS.md` — 本文件：架构说明、代码规范、测试与安全约定
- `docs/superpowers/README.md` — 设计文档索引（specs / plans）
- `docs/superpowers/specs/` — 设计规格文档，需用户批准后执行
- `docs/superpowers/plans/` — 实施计划文档，由 Agent 按任务执行
- `issues/open/` — 待修复的已知问题
- `issues/closed/` — 已修复的问题记录

## Development Workflow

> **Setup, run, build, and quality-gate commands are documented in [README.md](../README.md).**
> This section only covers conventions an Agent must follow.

### Quality Gates

Before committing or handing off work, run the full gate:

```bash
./scripts/check.sh
```

The quick gate (`./scripts/check-quick.sh`) runs Ruff, pytest with coverage (`fail_under = 85`), mypy, architecture contract checks (`scripts/check_architecture.py`), generated API type drift check (`npm run api:check`), frontend lint/typecheck/Vitest, and the spec health check (`scripts/verify_specs.py --check`). The full gate adds the production build.

Install the optional pre-commit hook:

```bash
./scripts/install-git-hooks.sh
```

## Code Style Guidelines

### Python

- **Formatter / Linter**: Ruff
- **Static Type Checker**: mypy (run `uv run mypy server/app`)
- **Line length**: 100 characters
- **Target Python version**: 3.11
- **Enabled lint rules**: `E`, `F`, `I`, `UP`, `B`, `SIM`
- **Ignored rule**: `E501` (line too long)
- Use Python 3.11+ syntax: union types with `|`, builtin generics like `dict[str, Any]`.
- Keep imports sorted; Ruff handles import sorting automatically.
- Avoid `print()` in production code; use `logging.getLogger(__name__)`.

### Frontend

- **Linter**: ESLint 10 with `typescript-eslint` and `eslint-plugin-react-hooks`
- **Formatter**: Prettier (`semi: false`, `singleQuote: true`, `tabWidth: 2`)
- The frontend uses React 18 with functional components and hooks.
- Keep components small and focused; extract reusable logic into helpers or stores.
- Prefer `useState(() => initialValue)` over `useState(initialValue)` for non-pure initializers.

## Runtime Architecture

### Backend

- `server.app.main:create_app(data_dir, start_worker)` is the application factory.
- When `start_worker=True`, two daemon threads may start on app lifespan:
  - `WorkerThread` polls the database every 1–3 seconds for videos in `queued` or `running` status.
  - `PipelineWorkerThread` polls for Agent Legion DAG jobs when `pipelines.enabled` is true in `config/pipeline.yaml`.
- The video worker starts in a **paused** state by default; call `POST /api/worker/resume` to begin processing.
- Each video has a `content_type` (`knowledge` or `question`) and progresses through a **type-specific pipeline**:

  **Knowledge videos** (`knowledge`):
  1. `download` — fetch the MP4
  2. `transcribe` — generate `subtitles.srt` and `transcription.json`
  3. `subtitle_review` — openclaw agent
  4. `chapter_generate` — openclaw agent
  5. `interaction_generate` — openclaw agent
  6. `content_review` — openclaw agent
  7. `assemble` — produce `metadata.json`, `report.md`, and `upload_params.json`
  8. `package` — mark as completed

  **Question explanation videos** (`question`) — skip interaction and content review:
  1. `download`
  2. `transcribe`
  3. `subtitle_review`
  4. `chapter_generate`
  5. `assemble`
  6. `package`

- The `assemble` phase also writes `upload_params.json` per video, transforming artifacts into the same format used by the `llm_claude` downstream pipeline:
  - subtitles → `sequence` + `start_time/end_time` in milliseconds + cleaned text
  - chapters → `clips_uuid` + `start_time/end_time` in milliseconds
  - interactions → split into `example_problem_trial` and `interaction_summary`, with options mapped to A/B/C/D keys and per-interaction `review_status` / `review_msg` extracted from `review_result.json`
- `upload_params.json` is included in the ZIP package alongside the other artifacts.

- Videos can be added with an empty URL. They are stored with `status: missing_url` and `current_phase: waiting_for_url`; the worker skips them until a URL is supplied later.
- If a phase fails, the video status becomes `failed` and the error is stored in the DB and log file.
- The API supports rerunning from any phase; rerunning clears artifacts for that phase and everything after it.
- Rerunning a `question` video from `interaction_generate` or `content_review` is automatically redirected to `assemble`.
- `DELETE /api/videos/{video_id}` removes the video record (cascading to `phase_runs` and `transcription_runs`) and deletes the local video directory including all artifacts.

### Frontend

- React 18 SPA with routes (`/` list view, `/videos/:id` detail view, `/workspaces`, `/workspaces/:workspaceId`) managed by React Router v6.
- State management via Zustand: `videoStore` (list & filtering), `detailStore` (selected video & artifacts), `uiStore` (dialogs, agent status, WebSocket), `artifactStore`, `interactionStore`, `packageStore`.
- UI built with `@material/web` Material 3 Web Components plus custom CSS.
- Fetches data from `/api/*` endpoints.
- UI labels are in Chinese (e.g., "加入队列", "重跑", "打包完成项").
- Video player supports clicking subtitle/chapter/interaction timestamps to seek.
- The add-video form includes a type selector (知识点 / 题目解析), an `external_id` field, and an optional `source_uuid`. Batch input supports `external_id,source_uuid` per line (source_uuid is optional).
- The video list and detail header display the content type label and `external_id`.
- The phase panel (`PhaseRunsPanel`) and rerun dropdown (`RerunDialog` / `RunToDialog`) adapt to the video's `content_type`; knowledge-only phases are hidden/disabled for `question` videos.
- A global `Toast` component displays feedback messages (e.g., "该资源正在被处理中").
- A **删除** button in the toolbar prompts for confirmation (`DeleteDialog` / `BatchDeleteDialog`) before calling `DELETE /api/videos/{video_id}` and clearing the selection.
- **Batch operations**: select multiple videos in the list to batch rerun, batch delete, or batch package.
- **Workspaces page** (`WorkspacesPage`): workspace selector, job batch creation, and job list for the Agent Legion pipeline. The workspace resources page (`WorkspaceResources`) allows configuring resource bindings, selecting the default entity (`question` / `video`), enabling/disabling intake modes, and overriding mode labels.

### Frontend Tooling

- **ESLint**: Configured in `frontend/eslint.config.js` with `@eslint/js`, `typescript-eslint`, and `eslint-plugin-react-hooks`.
- **Prettier**: Configured in `frontend/.prettierrc` (`semi: false`, `singleQuote: true`).
- **Lint scripts**: `npm run lint`, `npm run lint:fix`, `npm run format`, `npm run format:check`.

### Database

- SQLite with tables for both the video pipeline and the Agent Legion pipeline:
  - `videos` — video queue entries. Columns include `content_type` (`knowledge`|`question`), `external_id`, `knowledge_code`, `question_id`, `source_uuid`, `source_url`, `title`, `current_phase`, `status`, `duration`, `storage_dir`.
  - `phase_runs` — per-phase execution history for video pipeline
  - `transcription_runs` — transcription attempt history (whisper / SenseVoice)
  - `packages` — created package paths
  - `workspaces` — Agent Legion workspace definitions. Columns include `default_pipeline_key`, `cms_config_json`, `resource_config_json`, `default_entity` (default `'question'`), `intake_config_json`.
  - `job_batches` — batches of jobs created within a workspace
  - `jobs` — Agent Legion job entries with `pipeline_key`, `workspace_id`, `source_type`, `source_id`, `status`, `storage_dir`
  - `job_nodes` — per-job node execution status (`pending`, `running`, `completed`, `failed`, `stale`)
  - `node_runs` — per-node execution history for Agent Legion pipeline
- The DB initializer runs lightweight migrations (`alter table add column`) so existing tables gain new columns without data loss.
- `VideoQueries.connect()` and `JobQueries.connect()` are context managers (`@contextmanager`) that yield a `sqlite3.Connection` and ensure `conn.close()` is called after use.
- `delete_video()` performs cascading deletes: it removes matching rows from `phase_runs` and `transcription_runs` before deleting the `videos` row.

## Configuration

Edit `config/pipeline.yaml`:

- `asr.provider`: `auto`, `whisper`, or `sensevoice`.
- `asr.whisper.binary`: path to local `whisper-cli`.
- `asr.whisper.model`: path to local whisper model (e.g., `ggml-medium.bin`).
- `asr.whisper.vad_model`: optional path to VAD model for voice activity detection (e.g., `ggml-silero-v6.2.0.bin`). When set, whisper-cli runs with `--vad --vad-model`.
- `asr.sensevoice.script`: path to SenseVoice transcription script.
- `asr.sensevoice.model_dir`: path to `SenseVoiceSmall` model directory.
- `openclaw.command_template`: argument list with placeholders `{prompt_file}`, `{video_id}`, `{video_dir}`.
- `openclaw.cwd`: working directory for openclaw execution.
- `openclaw.timeout_seconds`: per-phase timeout (default 600).
- `openclaw.runners`: explicit list of runner definitions. Each item can include:
  - `command_template`: the argument list for this runner (same placeholders as above).
  - `count` (optional, default `1`): how many identical runners to create from this template. Use this to scale concurrency without duplicating the entire configuration block.
- `pipelines.enabled`: set to `true` to enable the Agent Legion DAG pipeline worker.

Pipeline definitions live in `config/pipelines/` (e.g., `question_content.yaml`). Each definition specifies:
- `key` and `label`
- `concurrency` (`local`, `agent`) — runner pool limits
- `nodes` — DAG nodes with `runner` (`local` or `agent`), `after` (dependencies), `inputs`, and `outputs`
- `intake` — optional intake configuration with `modes` mapping. Each mode has `label`, `input_field`, and optional `resource` (for CMS resolver lookups). The backend resolves `(entity, mode_key)` to a resolver at runtime via `RESOLVER_MAP`.

In `auto` ASR mode, the pipeline tries whisper.cpp first and falls back to SenseVoice if the SRT is missing, empty, unparsable, too short for the video, or obviously repetitive.

## Video Identity Fields

Each video carries identity fields that flow into `metadata.json` and the package `manifest.json`:

- `content_type` — `knowledge` or `question`
- `external_id` — the knowledge code or question ID supplied at intake
- `knowledge_code` — populated from `external_id` when `content_type == "knowledge"`
- `question_id` — populated from `external_id` when `content_type == "question"`
- `source_uuid` — optional CMS source UUID, supplied at intake or left empty

Question explanation videos do not produce interaction nodes. Their `metadata.json` keeps `nodes: []`, and `interactions.json` is written as an empty stub if it does not exist.

## Testing Instructions

- Tests live in `tests/` and use pytest.
- `pythonpath = ["."]` is configured in `pyproject.toml` so imports like `server.app.db` resolve.
- Coverage is enforced in `check-quick.sh` with `fail_under = 85` (configured in `pyproject.toml`).
- API tests use `fastapi.testclient.TestClient` with a temporary `data_dir`. The `client` fixture must use `with TestClient(app) as c:` to ensure lifespan resources are properly closed.
- Worker tests inject mock `TranscriptionProvider` implementations to avoid requiring real ASR binaries.
- Core tests validate SRT parsing, artifact cleanup, openclaw runner behavior, ZIP packaging, type-specific pipeline routing, pipeline definition loading, DAG scheduling, and job API endpoints.

Run the full suite with:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest -q
```

Run with coverage report:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest -q --cov=server --cov-report=term-missing
```

## Security Considerations

- The backend downloads arbitrary URLs via `requests`; only run with trusted input.
- OpenClaw commands are executed via `subprocess.run` with user-defined templates in `config/pipeline.yaml`. Ensure the configuration file is not writable by untrusted users.
- The SQLite database and video storage are local; there is no authentication layer. Do not expose the dev server to untrusted networks.
- `data/` is gitignored; never commit runtime data or secrets.

## Workspace Executor Extension Rules

The required extension order is:

1. Add or reuse a business `capability` on the Pipeline Node.
2. Implement that capability in an Executor definition or adapter.
3. Allocate the Executor to the Workspace with a Workspace upper limit.
4. Bind the Node to one compatible allocated Executor.

Do not put OpenClaw skill names, Pi skill directories, command templates, or Agent kinds in a
Pipeline Node. Do not read `_futures` or create per-Workspace pools to make capacity decisions.
Routes must declare Pydantic response models; frontend transport types come from
`frontend/src/generated/api.ts` and are never handwritten twice.

Wrong and correct Pipeline Node declarations:

```yaml
# Wrong: Pipeline leaks Agent implementation details.
review_keywords:
  runner: pi
  skill: reading_analysis/review_keywords

# Correct: Pipeline declares business capability only.
review_keywords:
  capability: review_keywords
```

Run the architecture contract and generated API checks:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/check_architecture.py
cd frontend && npm run api:check
./scripts/check-quick.sh
```
