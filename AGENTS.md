# Video Hive — Agent Guide

## Project Overview

Video Hive is a local processing console for educational videos. It maintains a queue of video links, distinguishes between **knowledge videos** and **question explanation videos**, downloads and transcribes them, runs the applicable external agent stages through `openclaw`, previews partial and final artifacts in a web UI, and packages completed results into a ZIP with per-video JSON and a manifest.

The project is a monorepo with a Python FastAPI backend and a vanilla TypeScript frontend built with Vite.

## Technology Stack

- **Backend**: Python 3.11+, FastAPI, Uvicorn, SQLite, PyYAML, Requests
- **Frontend**: TypeScript, Vite (no framework — plain DOM manipulation)
- **Package Management**: `uv` for Python; `npm` for the frontend
- **Linting / Formatting**: Ruff (Python)
- **Testing**: pytest

## Project Structure

```
video-hive/
├── pyproject.toml              # Python project metadata, dependencies, tool config
├── uv.lock                     # Locked Python dependency tree
├── config/
│   └── pipeline.yaml           # Runtime configuration (ASR providers, openclaw)
├── server/
│   ├── app/
│   │   ├── main.py             # FastAPI app factory + lifespan worker thread
│   │   ├── api.py              # REST API routes
│   │   ├── db.py               # SQLite database wrapper
│   │   ├── settings.py         # Settings loader from YAML
│   │   ├── worker.py           # Background worker loop + per-video phase processing
│   │   └── pipeline/           # Pipeline stage implementations
│   │       ├── common.py       # URL-to-id parsing, SRT parse/format helpers
│   │       ├── phases.py       # Phase list and agent-phase definitions
│   │       ├── download.py     # HTTP video downloader
│   │       ├── transcribe.py   # ASR providers (whisper.cpp / SenseVoice) + fallback logic
│   │       ├── openclaw.py     # OpenClaw command runner
│   │       ├── assemble.py     # Final metadata.json assembly
│   │       ├── artifacts.py    # Artifact cleanup on rerun
│   │       ├── reader.py       # Artifact reader for the API
│   │       ├── package.py      # ZIP packaging of completed videos
│   │       └── references/     # Markdown prompt references for openclaw phases
│   │           ├── phase-03-subtitle-review.md
│   │           ├── phase-04-chapter-generate.md
│   │           ├── phase-05-interaction-generate.md
│   │           └── phase-06-content-review.md
├── frontend/
│   ├── package.json
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.ts             # All UI logic (single file)
│       └── styles.css
├── tests/
│   ├── test_core.py            # Pipeline, DB, and provider unit tests
│   ├── test_api.py             # FastAPI endpoint tests with TestClient
│   └── test_worker.py          # Worker-phase integration tests
└── data/                       # Runtime data (gitignored)
    ├── video_hive.sqlite
    ├── videos/
    ├── logs/
    └── packages/
```

## Build and Run Commands

### Setup

```bash
uv sync
cd frontend
npm install
```

### Run Backend (with auto-reload and background worker)

```bash
UV_CACHE_DIR=.uv-cache uv run uvicorn server.app.main:app --reload --port 8000
```

### Run Frontend (development)

```bash
cd frontend
npm run dev
```

The Vite dev server runs on `127.0.0.1`. In development you may need a proxy to avoid CORS issues when the frontend calls the backend API.

### Production-Style Frontend Build

```bash
cd frontend
npm run build
```

After `frontend/dist` exists, the FastAPI backend serves it automatically from the same origin.

### Lint

```bash
UV_CACHE_DIR=.uv-cache uv run ruff check .
```

### Test

```bash
UV_CACHE_DIR=.uv-cache uv run pytest -q
```

### Quality Gates

For normal local development, run the quick gate:

```bash
./scripts/check-quick.sh
```

Before committing, handing work off, or claiming a cross-stack change is complete, run the full gate:

```bash
./scripts/check.sh
```

The quick gate runs Ruff, pytest, and frontend Vitest. The full gate runs the quick gate plus the frontend production build.

To install the optional local pre-commit hook that runs the quick gate before each commit:

```bash
./scripts/install-git-hooks.sh
```

## Code Style Guidelines

- **Formatter / Linter**: Ruff
- **Line length**: 100 characters
- **Target Python version**: 3.11
- **Enabled lint rules**: `E`, `F`, `I`, `UP`, `B`, `SIM`
- **Ignored rule**: `E501` (line too long)
- Use Python 3.11+ syntax: union types with `|`, builtin generics like `dict[str, Any]`.
- Keep imports sorted; Ruff handles import sorting automatically.
- The frontend is a single vanilla TypeScript file with no framework. Keep DOM manipulation imperative and straightforward.

## Runtime Architecture

### Backend

- `server.app.main:create_app(data_dir, start_worker)` is the application factory.
- When `start_worker=True`, a daemon thread starts on app lifespan and polls the database every 1–3 seconds for videos in `queued` or `running` status.
- Each video has a `content_type` (`knowledge` or `question`) and progresses through a **type-specific pipeline**:

  **Knowledge videos** (`knowledge`):
  1. `download` — fetch the MP4
  2. `transcribe` — generate `subtitles.srt` and `transcription.json`
  3. `subtitle_review` — openclaw agent
  4. `chapter_generate` — openclaw agent
  5. `interaction_generate` — openclaw agent
  6. `content_review` — openclaw agent
  7. `assemble` — produce `metadata.json` and `report.md`
  8. `package` — mark as completed

  **Question explanation videos** (`question`) — skip interaction and content review:
  1. `download`
  2. `transcribe`
  3. `subtitle_review`
  4. `chapter_generate`
  5. `assemble`
  6. `package`

- Videos can be added with an empty URL. They are stored with `status: missing_url` and `current_phase: waiting_for_url`; the worker skips them until a URL is supplied later.
- If a phase fails, the video status becomes `failed` and the error is stored in the DB and log file.
- The API supports rerunning from any phase; rerunning clears artifacts for that phase and everything after it.
- Rerunning a `question` video from `interaction_generate` or `content_review` is automatically redirected to `assemble`.
- `DELETE /api/videos/{video_id}` removes the video record (cascading to `phase_runs` and `transcription_runs`) and deletes the local video directory including all artifacts.

### Frontend

- Single-page app rendered into `#app` via plain `innerHTML` and event listeners.
- Fetches data from `/api/*` endpoints.
- UI labels are in Chinese (e.g., "加入队列", "重跑", "打包完成项").
- Video player supports clicking subtitle/chapter/interaction timestamps to seek.
- The add-video form includes a type selector (知识点 / 题目解析) and an `external_id` field.
- The video list and detail header display the content type label and `external_id`.
- The phase panel and rerun dropdown adapt to the video's `content_type`; knowledge-only phases are hidden/disabled for `question` videos.
- A **删除** button in the toolbar prompts for confirmation before calling `DELETE /api/videos/{video_id}` and clearing the selection.

### Database

- SQLite with four tables:
  - `videos` — queue entries. Columns include `content_type` (`knowledge`|`question`), `external_id`, `knowledge_code`, `question_id`, `source_url`, `title`, `current_phase`, `status`, `duration`, `storage_dir`.
  - `phase_runs` — per-phase execution history
  - `transcription_runs` — transcription attempt history (whisper / SenseVoice)
  - `packages` — created package paths
- The DB initializer runs lightweight migrations (`alter table add column`) so existing `videos` tables gain `content_type`, `external_id`, `knowledge_code`, and `question_id` without data loss.
- `delete_video()` performs cascading deletes: it removes matching rows from `phase_runs` and `transcription_runs` before deleting the `videos` row.

## Configuration

Edit `config/pipeline.yaml`:

- `asr.provider`: `auto`, `whisper`, or `sensevoice`.
- `asr.whisper.binary`: path to local `whisper-cli`.
- `asr.whisper.model`: path to local whisper model (e.g., `ggml-medium.bin`).
- `asr.sensevoice.script`: path to SenseVoice transcription script.
- `asr.sensevoice.model_dir`: path to `SenseVoiceSmall` model directory.
- `openclaw.command_template`: argument list with placeholders `{prompt_file}`, `{video_id}`, `{video_dir}`.
- `openclaw.cwd`: working directory for openclaw execution.
- `openclaw.timeout_seconds`: per-phase timeout (default 600).

In `auto` ASR mode, the pipeline tries whisper.cpp first and falls back to SenseVoice if the SRT is missing, empty, unparsable, too short for the video, or obviously repetitive.

## Video Identity Fields

Each video carries identity fields that flow into `metadata.json` and the package `manifest.json`:

- `content_type` — `knowledge` or `question`
- `external_id` — the knowledge code or question ID supplied at intake
- `knowledge_code` — populated from `external_id` when `content_type == "knowledge"`
- `question_id` — populated from `external_id` when `content_type == "question"`

Question explanation videos do not produce interaction nodes. Their `metadata.json` keeps `nodes: []`, and `interactions.json` is written as an empty stub if it does not exist.

## Testing Instructions

- Tests live in `tests/` and use pytest.
- `pythonpath = ["."]` is configured in `pyproject.toml` so imports like `server.app.db` resolve.
- API tests use `fastapi.testclient.TestClient` with a temporary `data_dir`.
- Worker tests inject mock `TranscriptionProvider` implementations to avoid requiring real ASR binaries.
- Core tests validate SRT parsing, artifact cleanup, openclaw runner behavior, ZIP packaging, and type-specific pipeline routing.

Run the full suite with:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest -q
```

## Security Considerations

- The backend downloads arbitrary URLs via `requests`; only run with trusted input.
- OpenClaw commands are executed via `subprocess.run` with user-defined templates in `config/pipeline.yaml`. Ensure the configuration file is not writable by untrusted users.
- The SQLite database and video storage are local; there is no authentication layer. Do not expose the dev server to untrusted networks.
- `data/` is gitignored; never commit runtime data or secrets.
