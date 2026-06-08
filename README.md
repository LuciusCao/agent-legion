# Video Hive

Local processing console for educational videos. It queues knowledge videos and question explanation videos, downloads and transcribes them, runs the applicable openclaw content stages, previews partial/final artifacts, and packages completed JSON for handoff.

## Current Shape

- Backend: FastAPI, SQLite, background worker.
- Python tooling: `uv` for dependency/runtime management, `ruff` for lint/format, `mypy` for type checking.
- Frontend: Vite + TypeScript, ESLint + Prettier.
- Storage: `data/video_hive.sqlite`, `data/videos/{video_id}/`, `data/logs/`, `data/packages/`, `data/jobs/`.
- Video queue model: each item has `content_type`, `external_id`, optional `source_url`, and phase/status fields.
- Agent Legion pipeline model: workspace-scoped DAG jobs with configurable pipeline definitions (`config/pipelines/`).

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

> For full build commands, quality gates, and development workflow, see [AGENTS.md](AGENTS.md).

## Configuration

Edit `config/pipeline.yaml`.

- `asr.provider`: `auto`, `whisper`, or `sensevoice`.
- `asr.whisper.binary`: local `whisper-cli`.
- `asr.whisper.model`: local whisper medium model.
- `asr.sensevoice.script`: SenseVoice SRT script.
- `asr.sensevoice.model_dir`: local `SenseVoiceSmall` model.
- `openclaw.command_template`: command argument list. Supported placeholders: `{prompt_file}`, `{video_id}`, `{video_dir}`.
- `openclaw.cwd`: working directory for openclaw.

In `auto` ASR mode, Video Hive tries whisper.cpp first and falls back to SenseVoice if the SRT is missing, empty, unparsable, too short for the video, or obviously repetitive.

> See [AGENTS.md](AGENTS.md) for full configuration reference.

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

Open the Vite URL shown by npm. The frontend calls the backend API on the same origin in production; for development, use a proxy if the browser blocks cross-origin API calls.

Production-style frontend build:

```bash
cd frontend
npm run build
```

After `frontend/dist` exists, the FastAPI backend serves it from `http://127.0.0.1:8000`.

> See [AGENTS.md](AGENTS.md) for full quality gates, test commands, and pre-commit hooks.

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

Agent Legion pipeline (workspace / job) endpoints:

- `GET /api/pipelines/{pipeline_key}` — pipeline definition metadata (includes `intake.modes` without `task_entity` / `resolver`)
- `GET /api/workspaces` — list workspaces
- `POST /api/workspaces` — create workspace (supports `default_entity` and `intake_config`)
- `GET /api/workspaces/{workspace_id}` — get workspace (returns `default_entity` and `intake_config`)
- `PATCH /api/workspaces/{workspace_id}` — update workspace (supports `default_entity` and `intake_config`)
- `POST /api/workspaces/{workspace_id}/job-batches` — create a batch of jobs (supports `entity`; defaults to workspace `default_entity`)
- `GET /api/workspaces/{workspace_id}/jobs` — list jobs in workspace
- `GET /api/jobs/{job_id}` — job detail with nodes, runs, artifacts
- `GET /api/jobs/{job_id}/artifacts/{artifact_name}` — read job artifact
- `POST /api/jobs/{job_id}/nodes/{node_key}/rerun` — rerun a node and mark downstream nodes stale
