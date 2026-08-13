"""video_knowledge node: assemble video metadata and derived artifacts.

Normalizes ``interactions.json`` into the dict shape the legacy assembler
expects, runs ``assemble_video`` over the job directory, and guarantees a
fallback ``report.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.pipeline.assemble import assemble_video
from server.app.video_capabilities.contracts import VideoKnowledgeInput
from workspace_libs.node_sdk import NodeContext


def _video_id(video_input: VideoKnowledgeInput, job: dict[str, Any]) -> str:
    return video_input.legacy_video_id or str(job.get("id") or "") or video_input.external_id


def _normalize_interactions(ctx: NodeContext) -> None:
    """Ensure interactions.json uses the dict shape legacy assemble_video expects."""
    if not ctx.artifacts.path("interactions.json").exists():
        return
    data = ctx.artifacts.read_json("interactions.json")
    if isinstance(data, list):
        ctx.artifacts.write_json("interactions.json", {"version": "1.0", "interactions": data})


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    ctx = NodeContext(job, job_dir, runtime)
    video_input = VideoKnowledgeInput.from_mapping(ctx.artifacts.read_json("video_input.json"))
    legacy_video = {
        "id": _video_id(video_input, job),
        "title": video_input.title,
        "source_url": video_input.source_url,
        "content_type": "knowledge",
        "external_id": video_input.external_id,
        "source_uuid": video_input.source_uuid,
    }
    _normalize_interactions(ctx)
    assemble_video(legacy_video, job_dir)
    if not (job_dir / "report.md").exists():
        ctx.artifacts.write_text("report.md", f"# {video_input.title or legacy_video['id']}\n")
