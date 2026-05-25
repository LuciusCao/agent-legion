# Video Hive

Local processing console for educational videos. It queues knowledge videos and question explanation videos, downloads and transcribes them, runs the applicable openclaw content stages, previews partial/final artifacts, and packages completed JSON for handoff.

## Current Shape

- Backend: FastAPI, SQLite, background worker.
- Python tooling: `uv` for dependency/runtime management, `ruff` for lint.
- Frontend: Vite + TypeScript.
- Storage: `data/video_hive.sqlite`, `data/videos/{video_id}/`, `data/logs/`, `data/packages/`.
- Queue model: each item has `content_type`, `external_id`, optional `source_url`, and phase/status fields.

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

## Configuration

Edit `config/pipeline.yaml`.

- `asr.provider`: `auto`, `whisper`, or `sensevoice`.
- `asr.whisper.binary`: local `whisper-cli`.
- `asr.whisper.model`: local whisper medium model.
- `asr.sensevoice.script`: SenseVoice SRT script. The default points at the existing `cms-extensions/engineering/pipeline/scripts/transcribe_sensevoice.py` implementation.
- `asr.sensevoice.model_dir`: local `SenseVoiceSmall` model.
- `openclaw.command_template`: command argument list. Supported placeholders: `{prompt_file}`, `{video_id}`, `{video_dir}`.
- `openclaw.cwd`: working directory for openclaw.

In `auto` ASR mode, Video Hive tries whisper.cpp first and falls back to SenseVoice if the SRT is missing, empty, unparsable, too short for the video, or obviously repetitive.

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
UV_CACHE_DIR=.uv-cache uv run uvicorn server.app.main:app --reload --port 8000
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

## Quality Gates

Quick local check for normal development:

```bash
./scripts/check-quick.sh
```

Full check before committing or handing work off:

```bash
./scripts/check.sh
```

The quick gate runs Ruff, backend tests, and frontend tests. The full gate runs the quick gate plus the production-style frontend build.

Install the optional local Git pre-commit hook to run the quick gate before each commit:

```bash
./scripts/install-git-hooks.sh
```

Equivalent commands:

```bash
UV_CACHE_DIR=.uv-cache uv run ruff check .
UV_CACHE_DIR=.uv-cache uv run pytest -q
cd frontend
npm run test
npm run build
```

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

Useful endpoints:

- `POST /api/videos`
- `GET /api/videos`
- `GET /api/videos/{video_id}`
- `DELETE /api/videos/{video_id}` deletes the queue record and that video's local storage directory.
- `POST /api/videos/{video_id}/rerun`
- `GET /api/videos/{video_id}/artifacts`
- `GET /api/videos/{video_id}/logs`
- `POST /api/worker/tick` processes one local non-agent phase; agent phases are handled by the background worker runner pool.
- `POST /api/package`
