"""video_knowledge node: assemble video metadata and derived artifacts.

Normalizes ``interactions.json`` into the dict shape the legacy assembler
expects, runs ``assemble_video`` over the job directory, and guarantees a
fallback ``report.md``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from server.app.pipeline.assemble import assemble_video
from server.app.video_capabilities.contracts import VideoKnowledgeInput

logger = logging.getLogger(__name__)


def _load_video_input(job_dir: Path) -> VideoKnowledgeInput:
    raw = json.loads((job_dir / "video_input.json").read_text(encoding="utf-8"))
    return VideoKnowledgeInput.from_mapping(raw)


def _video_id(video_input: VideoKnowledgeInput, job: dict[str, Any]) -> str:
    return video_input.legacy_video_id or str(job.get("id") or "") or video_input.external_id


def _normalize_interactions(video_dir: Path) -> None:
    """Ensure interactions.json uses the dict shape legacy assemble_video expects."""
    interactions_path = video_dir / "interactions.json"
    if not interactions_path.exists():
        return
    data = json.loads(interactions_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        interactions_path.write_text(
            json.dumps({"version": "1.0", "interactions": data}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    video_input = _load_video_input(job_dir)
    legacy_video = {
        "id": _video_id(video_input, job),
        "title": video_input.title,
        "source_url": video_input.source_url,
        "content_type": "knowledge",
        "external_id": video_input.external_id,
        "source_uuid": video_input.source_uuid,
    }
    _normalize_interactions(job_dir)
    assemble_video(legacy_video, job_dir)
    if not (job_dir / "report.md").exists():
        (job_dir / "report.md").write_text(
            f"# {video_input.title or legacy_video['id']}\n", encoding="utf-8"
        )
