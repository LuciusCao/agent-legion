"""First node of video_knowledge: resolve the input source and download the MP4.

Knowledge-mode intake writes an opaque ``source_ref``; this node resolves it
against the CMS through the resource binding + vault chain (spec D16), writes
the resolved fields back to ``video_input.json`` so downstream nodes
(assemble) see the same fields as the urls intake mode, then downloads
``source.mp4``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from server.app.cms.client import get_token
from server.app.cms.knowledge import lookup_knowledge_video
from server.app.pipeline.download import download_video as legacy_download_video
from server.app.video_capabilities.contracts import VideoKnowledgeInput
from server.app.workflows.cms_helpers import _effective_cms_config

logger = logging.getLogger(__name__)


def _load_video_input(job_dir: Path) -> VideoKnowledgeInput:
    raw = json.loads((job_dir / "video_input.json").read_text(encoding="utf-8"))
    return VideoKnowledgeInput.from_mapping(raw)


def _resolve_knowledge_source(
    job: dict[str, Any],
    job_dir: Path,
    video_input: VideoKnowledgeInput,
    context: dict[str, Any],
) -> VideoKnowledgeInput:
    """Resolve an opaque knowledge ``source_ref`` against the CMS."""
    if not video_input.source_ref:
        raise RuntimeError("video source_url is empty and no source_ref is set")
    cms_config = _effective_cms_config(job, context, resource_key="knowledge_video")
    api_url = str(cms_config.get("api_url") or cms_config.get("knowledge_url") or "")
    token = get_token(str(cms_config.get("env", "")), cms_config)
    lookup = lookup_knowledge_video(video_input.source_ref, api_url, token)
    if lookup.status == "not_found":
        raise RuntimeError(f"knowledge video not found: {video_input.source_ref}")
    source_url = str(lookup.url or "").strip()
    if not source_url:
        raise RuntimeError(f"knowledge video has no source url: {video_input.source_ref}")
    resolved = replace(
        video_input,
        source_url=source_url,
        source_uuid=str(lookup.source_uuid or ""),
        title=lookup.title or video_input.title,
    )
    (job_dir / "video_input.json").write_text(
        json.dumps(asdict(resolved), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return resolved


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    video_input = _load_video_input(job_dir)
    if not video_input.source_url:
        video_input = _resolve_knowledge_source(job, job_dir, video_input, runtime or {})
    output_path = job_dir / "source.mp4"
    legacy_download_video(video_input.source_url, output_path)
