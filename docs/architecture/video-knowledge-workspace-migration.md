# Video Knowledge Workspace Migration

This document summarizes the architecture for migrating knowledge videos from the legacy Video Hive runtime to the Agent Legion Workspace runtime.

## Overview

Knowledge videos (`content_type=knowledge`) are produced by a dedicated Workspace workflow instead of the legacy `videos` / `phase_runs` state machine. The migration keeps the legacy runtime frozen, moves new knowledge-video work into Workspace Jobs, and provides a one-time CLI to copy existing legacy artifacts into the new model.

## Components

### `video_knowledge` Workflow

`config/workflows/video_knowledge.yaml` declares the Workspace DAG for knowledge videos. Nodes declare only business capabilities (for example, `download`, `transcribe`, `subtitle_review`, `chapter_generate`, `interaction_generate`, `content_review`, `assemble`) and do not name runners, agents, skills, or command templates.

### `server/app/video_capabilities/` Layer

The `video_capabilities` package provides execution-neutral contracts and adapters between video-facing application code and Workspace nodes:

- `contracts.py` — knowledge-video input contract types (`VideoKnowledgeInput`).
- `response_contracts.py` — stable response shapes for video-facing routes.
- `projection.py` — read projections over Workspace job artifacts for the video UI.
- `_video_paths.py` — canonical video path/URL resolution helpers.

This layer keeps generic Workspace scheduler, lease, executor, and job services free of video-specific branches.

### Migration CLI

> **Stale paths (2026-07-22):** `scripts/migrate-video-hive-to-agent-legion.py` 不存在（从未落地或已删除），以下 Migration CLI 小节内容已失效，保留仅作历史参考。

`scripts/migrate-video-hive-to-agent-legion.py` performs local single-user upgrades:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/migrate-video-hive-to-agent-legion.py --check
UV_CACHE_DIR=.uv-cache uv run python scripts/migrate-video-hive-to-agent-legion.py --apply
```

`--check` is read-only and prints a report. `--apply` stops being safe only when the application is stopped first; it backs up SQLite, copies legacy video artifacts into `data/jobs/...`, creates Workspace Jobs in `video_knowledge`, and writes a migration report under `data/backups/`.

### UI Changes

The Job Detail page renders video artifacts through `VideoContentPanel` (`frontend/src/components/VideoContentPanel.tsx`). This component reads persisted Workspace job artifacts and replaces legacy video-detail views.

## Boundaries

- Generic Workspace code does not read legacy `videos` / `phase_runs` tables.
- No new behavior is added under `/api/videos` or `/video-hive`.
- New knowledge-video submissions create Workspace Jobs only.
- Question videos remain out of scope and are not handled by `video_knowledge`.

## Quality Impact

- The `video_capabilities` contract layer is covered by dedicated contract, adapter, and artifact-equivalence tests.
- The migration CLI is verified with `--check` before `--apply` and always produces a timestamped SQLite backup.
- Frontend Job Detail rendering is exercised through `VideoContentPanel` unit tests.
- Documentation changes do not alter runtime behavior; they are validated by `scripts/verify_specs.py --check` and the normal quality gates.
