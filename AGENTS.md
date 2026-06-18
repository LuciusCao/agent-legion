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
│   └── workflow.yaml           # Runtime configuration (ASR providers, openclaw, workflows)
├── server/
│   ├── app/
│   │   ├── main.py             # FastAPI app factory + lifespan worker threads
│   │   ├── routes/             # REST API routes (videos, agents, worker, artifacts, packages, jobs, workspaces)
│   │   │   ├── workspace_configuration.py # Executor allocations, bindings, and local node limits
│   │   │   ├── workspace_executors.py     # Executor registry and workspace allocations
│   │   │   ├── workspace_runs.py          # Node run lifecycle and rerun
│   │   │   └── workspace_settings.py      # Workspace resource/intake settings
│   │   ├── db/                 # SQLite database wrapper (schema, queries, notifications)
│   │   │   └── migrations/     # Versioned schema migrations (v001–v005, registry, runner, report)
│   │   ├── executors/          # Phase 5 executor runtime (registry, runtime, config, pi, openclaw, local, leases, scheduling, legacy_migration)
│   │   │   ├── runtime_config.py # Executor runtime configuration loader
│   │   ├── cms/                # CMS API integration (auth, client, knowledge, question)
│   │   ├── jobs/               # Job queries for Agent Legion workflow
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
│   │   ├── workflow_worker_thread.py # Agent Legion workflow worker thread
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
│   │   └── workflows/          # Agent Legion DAG workflow definitions
│   │       ├── definition.py   # Workflow definition loader
│   │       ├── executor.py     # Workflow node executor
│   │       ├── scheduler.py    # DAG scheduling and downstream node resolution
│   │       ├── registry.py     # Workflow definition registry by key
│   │       ├── reading_analysis.py # Reading analysis local node handlers
│   │       ├── question_content.py # Question content workflow presets
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
├── scripts/                    # Quality gates, migration finalizers, and git hooks
│   ├── check_architecture.py
│   └── finalize-workspace-executor-migration.py
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

The quick gate (`./scripts/check-quick.sh`) runs Ruff, validates the architecture invariant
and exemption registries (`scripts/check_invariants.py`), pytest with coverage
(`fail_under = 85`) ignoring the higher-fidelity directories, mypy, architecture contract
checks (`scripts/check_architecture.py`), generated API type drift check
(`npm run api:check`), frontend lint/typecheck/Vitest, and the spec health check
(`scripts/verify_specs.py --check`). The full gate adds `pytest -q tests/full -m full_gate`
and the production build. The CI gate (`./scripts/check-ci.sh`) runs the full gate and then
`pytest -q tests/ci -m ci_extended`.

Install the optional pre-commit hook:

```bash
./scripts/install-git-hooks.sh
```

### Architecture Quality Workflow

The project maintains three layers of architecture evidence. Every spec or plan that changes
boundaries, concurrency, security, or long-lived data must include a `Quality Impact` section
that names the affected architecture invariant IDs and any new exemptions.

- **Registry impact**: Before adding code that crosses a layer boundary or increases a module
  budget, update `config/architecture/architecture-invariants.yaml` with the new invariant and its owner.
  If the change needs a temporary allowance, add a governed exemption to
  `config/architecture/architecture-exemptions.yaml` instead of editing `config/architecture/architecture-budgets.json`
  directly.

- **Required `Quality Impact` section**: Specs and plans under `docs/superpowers/` must include
  a `Quality Impact` subsection that lists:
  - Invariant IDs created, changed, or risked by the work.
  - The gate layer (`quick`, `full`, or `ci_extended`) that will carry the evidence.
  - Any new exemption with `check`, `path`, `reason`, `owner`, and `remove_when`.

- **Evidence placement**:
  - `quick`: Static analysis, unit tests, and contract checks under `tests/` and `scripts/`.
  - `full`: Deterministic higher-fidelity scenarios under `tests/full/` marked with
    `@pytest.mark.full_gate`. Run by `scripts/check.sh`.
  - `ci_extended`: Repeated stress or failure-injection scenarios under `tests/ci/` marked with
    `@pytest.mark.ci_extended`. Run by `scripts/check-ci.sh` after the full gate.

- **Critical evidence rules**: `critical` risk invariants require at least one local runtime
  evidence target in the `quick` gate and one full runtime evidence target in the `full` gate.
  `ci_extended` evidence alone never satisfies a critical invariant.

- **Exemption lifecycle**: Each exemption must contain `check`, `path`, `reason`, `owner`, and
  `remove_when`. Wildcard-only paths, vague reasons (`legacy`, `temporary`, `follow up`), missing
  owners, and untracked removal conditions are rejected by `scripts/check_invariants.py`. The
  `remove_when` field must reference either a tracked plan section
  (`docs/superpowers/plans/...`) or an issue file (`issues/open/...` or `issues/closed/...`).

- **Two-stage review**: The author runs `./scripts/check-quick.sh` before requesting review.
  The reviewer verifies the `Quality Impact` section and runs `./scripts/check.sh` before
  approval. CI runs `./scripts/check-ci.sh`.

### Phase 5 Workspace Executor Migration

When opening a pre-Phase-5 database, run the one-time finalizer before starting the server:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/finalize-workspace-executor-migration.py --check
UV_CACHE_DIR=.uv-cache uv run python scripts/finalize-workspace-executor-migration.py --apply
```

- `--check` is read-only and emits a deterministic JSON report. An empty `issues` list means the
  destructive cleanup migration can proceed safely.
- `--apply` creates a timestamped SQLite backup beside `data/video_hive.sqlite` and then migrates
  legacy Workspace Agent/Workflow settings into Executor allocations, bindings, and local Node
  limits.

If the report lists unknown legacy Agent IDs, either configure an equivalent Executor in
`config/workflow.yaml` or manually remediate the `workspace_agent_assignments` rows before
retrying. The app aborts startup with the report and the exact `--check` command when finalization
is blocked.

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
  - `WorkflowWorkerThread` polls for Agent Legion DAG jobs when `workflows.enabled` is true in `config/workflow.yaml`.
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
- **Workspaces page** (`WorkspacesPage`): workspace selector, job batch creation, and job list for the Agent Legion workflow. The workspace resources page (`WorkspaceResources`) allows configuring resource bindings, selecting the default entity (`question` / `video`), enabling/disabling intake modes, and overriding mode labels.

### Frontend Tooling

- **ESLint**: Configured in `frontend/eslint.config.js` with `@eslint/js`, `typescript-eslint`, and `eslint-plugin-react-hooks`.
- **Prettier**: Configured in `frontend/.prettierrc` (`semi: false`, `singleQuote: true`).
- **Lint scripts**: `npm run lint`, `npm run lint:fix`, `npm run format`, `npm run format:check`.

### Database

- SQLite with tables for both the video pipeline and the Agent Legion workflow:
  - `videos` — video queue entries. Columns include `content_type` (`knowledge`|`question`), `external_id`, `knowledge_code`, `question_id`, `source_uuid`, `source_url`, `title`, `current_phase`, `status`, `duration`, `storage_dir`.
  - `phase_runs` — per-phase execution history for video pipeline
  - `transcription_runs` — transcription attempt history (whisper / SenseVoice)
  - `packages` — created package paths
  - `workspaces` — Agent Legion workspace definitions. Columns include `default_workflow_key`, `cms_config_json`, `resource_config_json`, `default_entity` (default `'question'`), `intake_config_json`.
  - `job_batches` — batches of jobs created within a workspace
  - `jobs` — Agent Legion job entries with `workflow_key`, `workspace_id`, `source_type`, `source_id`, `status`, `storage_dir`
  - `job_nodes` — per-job node execution status (`pending`, `running`, `completed`, `failed`, `stale`)
  - `node_runs` — per-node execution history for Agent Legion workflow
- The DB initializer runs lightweight migrations (`alter table add column`) so existing tables gain new columns without data loss.
- `VideoQueries.connect()` and `JobQueries.connect()` are context managers (`@contextmanager`) that yield a `sqlite3.Connection` and ensure `conn.close()` is called after use.
- `delete_video()` performs cascading deletes: it removes matching rows from `phase_runs` and `transcription_runs` before deleting the `videos` row.

## Configuration

Edit `config/workflow.yaml`:

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
- `workflows.enabled`: set to `true` to enable the Agent Legion DAG workflow worker.

Workflow definitions live in `config/workflows/` (e.g., `question_content.yaml`). Each definition specifies:
- `key` and `label`
- `nodes` — DAG nodes declaring only `capability`, `after` (dependencies), `inputs`, `outputs`, and optional `label`. They never declare a `runner`, `agent`, `skill`, or command template.
- `intake` — optional intake configuration with `modes` mapping. Each mode has `label`, `input_field`, and optional `resource` (for CMS resolver lookups). The backend resolves `(entity, mode_key)` to a resolver at runtime via `RESOLVER_MAP`.

Executor allocations, bindings, and local node limits are configured at the Workspace level through the workspace executor routes (`workspace_executors.py`, `workspace_configuration.py`) and the `executors` runtime configuration. See `server/app/executors/` for the registry, runtime, lease management, and typed Executor implementations (`local`, `pi`, `openclaw`).

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
- Core tests validate SRT parsing, artifact cleanup, openclaw runner behavior, ZIP packaging, type-specific pipeline routing, workflow definition loading, DAG scheduling, and job API endpoints.

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
- OpenClaw commands are executed via `subprocess.run` with user-defined templates in `config/workflow.yaml`. Ensure the configuration file is not writable by untrusted users.
- The SQLite database and video storage are local; there is no authentication layer. Do not expose the dev server to untrusted networks.
- `data/` is gitignored; never commit runtime data or secrets.

## Workspace API Extension Rules

1. Add or update a Pydantic model in `routes/job_contracts.py`.
2. Add orchestration to the owning service; services never import FastAPI.
3. Add a thin handler to the owning focused router.
4. Do not add CMS, artifact mutation, multi-write coordination, or router composition to `routes/jobs.py`.
5. Run route-manifest, architecture, generated-contract, and full quality gates.

## Workspace Executor Extension Rules

The required extension order is:

1. Add or reuse a business `capability` on the Workflow Node.
2. Add an implementation under a typed Executor definition.
3. Allocate the Executor to the Workspace with a Workspace upper limit.
4. Bind the Node to one compatible allocated Executor.
5. Add a local Node limit only when the bound Executor kind is local.

Do not put OpenClaw skill names, Pi skill directories, command templates, or Agent kinds in a
Workflow Node. Do not read `_futures` or create per-Workspace pools to make capacity decisions.
Routes must declare Pydantic response models; frontend transport types come from
`frontend/src/generated/api.ts` and are never handwritten twice.

Wrong and correct Workflow Node declarations:

```yaml
# Wrong: Workflow leaks Agent implementation details.
review_keywords:
  runner: pi
  skill: reading_analysis/review_keywords

# Correct: Workflow declares business capability only.
review_keywords:
  capability: review_keywords
```

Other wrong patterns the architecture gate rejects:

```python
# Wrong: Scheduler imports a concrete agent runner.
from server.app.workflows.pi_runner import PiRunner
```

```yaml
# Wrong: Agent-bound Node has a workspace_node_limits entry.
concurrency:
  nodes:
    review_keywords: 2
nodes:
  review_keywords:
    runner: agent
```

```python
# Wrong: Capacity decision based on future count.
if len(self._futures) < self.capacity:
    ...
```

```python
# Wrong: Implicit fallback when binding is absent.
binding = get_binding(...)
executor_id = binding["executor_id"] if binding else "local-default"
```

Workspace Executor configuration extensions must:

- add typed Pydantic transport fields;
- validate bindings by capability;
- replace allocations, bindings, and local limits in one transaction;
- derive frontend transport types from generated OpenAPI;
- never add Node limits to agent-backed Executors;
- never reintroduce Workspace-specific thread pools or runner-based binding logic.

Recovery behavior: on startup and before every scheduling pass the worker expires stale
executor leases. A stale lease marks its `node_run` as failed, its `job_node` as stale (so an
explicit user rerun can recover it), and the job as failed. It never resets failed Nodes or
automatically reruns them.

Run the focused Phase 3/5 executor governance tests:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest -q \
  tests/test_executor_recovery.py \
  tests/test_check_architecture.py \
  tests/test_architecture_baselines.py \
  tests/test_architecture_executor_governance.py \
  tests/test_executor_phase5_inventory.py
```

Run the architecture contract and generated API checks:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/check_architecture.py
cd frontend && npm run api:check
./scripts/check-quick.sh
```

## Phase 6 Workspace Job Boundary Rules

Generic Workspace Job code must keep Video Hive phases, direct Executor invocation, DAG traversal,
and filesystem deletion out of the route layer.

```text
Job UI reads persisted Node state -> mutation calls service -> scheduler claims through leases
```

Rules:

- Generic Workspace routes/services (`routes/jobs.py`, `routes/job_*.py`, `routes/workspace_*.py`,
  `services/job_*.py`) must not import Video Hive phase modules (`server.app.pipeline.*` singular)
  or video services (`services.video_actions`, `services.intake`, `services.manual_run`,
  `services.interaction_stats`).
- Job execution services must claim capacity through `server.app.executors.leases`; they must not
  directly import or invoke Executor adapters (`executors.local`, `.pi`, `.openclaw`, `.runtime`,
  `.registry`).
- Route modules must not perform DAG traversal (`downstream_nodes`, `ancestor_closure`,
  `find_ready_nodes`) or filesystem deletion (`shutil.rmtree`, `os.remove`, `Path.unlink`, etc.).
  Those belong in services.
- Frontend transport types for Job/Workspace responses must be derived from
  `frontend/src/generated/api.ts` (via `components['schemas']` or `ApiSchemas['...']`), not
  handwritten duplicates.
- Schema mutation (`CREATE TABLE`, `ALTER TABLE`, `DROP TABLE`, `CREATE INDEX`) belongs in
  `server/app/db/migrations/` or `server/app/db/schema.py`.

Wrong examples the architecture gate rejects:

```python
# Wrong: Generic Workspace route imports a Video Hive phase.
from server.app.pipeline.download import download_video
```

```python
# Wrong: Job service invokes an Executor directly.
from server.app.executors.local import LocalExecutor
LocalExecutor(...).execute(context)
```

```python
# Wrong: Route module traverses the DAG or deletes files.
from server.app.workflows.scheduler import downstream_nodes
shutil.rmtree(storage_dir)
```

```typescript
// Wrong: Handwritten transport type duplicating a generated response.
export type JobSummaryResponse = { id: string }
```

```python
# Wrong: Schema mutation outside migrations/schema.
conn.execute("alter table jobs add column x text")
```

Run the focused Phase 6 governance and control-flow tests:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest -q \
  tests/test_architecture_phase6.py \
  tests/test_check_architecture.py \
  tests/test_workspace_job_control_flow.py
```

Before committing or handing off work, run the full gate:

```bash
./scripts/check.sh
```
