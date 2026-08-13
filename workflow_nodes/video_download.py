"""First node of video_knowledge: resolve the input source and download the MP4.

Knowledge-mode intake writes an opaque ``source_ref``; this node resolves it
against the CMS through the node config chain (config_schema defaults plus
node/workspace overrides; the connection config and token arrive resolved
from the instance-level external connection at dispatch), writes the resolved
fields back to ``video_input.json`` so
downstream nodes (assemble) see the same fields as the urls intake mode,
then downloads ``source.mp4``.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from server.app.pipeline.download import download_video as legacy_download_video
from server.app.video_capabilities.contracts import VideoKnowledgeInput
from workspace_libs.cms import urls as cms_urls
from workspace_libs.cms.client import CmsClientError, get_token
from workspace_libs.cms.knowledge import lookup_knowledge_video
from workspace_libs.node_sdk import NodeContext

# Retired node-level connection keys (pre-connection era). They are honored
# only when no connection was injected (legacy frozen payloads); a resolved
# connection always wins.
_LEGACY_CONNECTION_KEYS = ("token", "env", "base_url", "api_url", "knowledge_url")


def _load_video_input(ctx: NodeContext) -> VideoKnowledgeInput:
    return VideoKnowledgeInput.from_mapping(ctx.artifacts.read_json("video_input.json"))


def _resolve_knowledge_source(
    ctx: NodeContext,
    video_input: VideoKnowledgeInput,
) -> VideoKnowledgeInput:
    """Resolve an opaque knowledge ``source_ref`` against the CMS."""
    if not video_input.source_ref:
        raise RuntimeError("video source_url is empty and no source_ref is set")
    cms_config = ctx.service_config(legacy_keys=_LEGACY_CONNECTION_KEYS)
    api_url = str(
        cms_config.get("api_url")
        or cms_config.get("knowledge_url")
        or cms_urls.knowledge_url(cms_config)
    )
    token = get_token(str(cms_config.get("env", "")), cms_config)
    try:
        lookup = lookup_knowledge_video(video_input.source_ref, api_url, token)
    except CmsClientError as exc:
        # Only auth-semantics failures (HTTP 401/403, known in-band auth
        # codes) invalidate the cached connection token; transport and
        # non-auth in-band errors leave the healthy token alone. The node
        # only records the fact; the parent executor invalidates.
        if exc.auth_failure:
            ctx.report_auth_failure()
        raise
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
    ctx.artifacts.write_json("video_input.json", asdict(resolved))
    return resolved


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    ctx = NodeContext(job, job_dir, runtime)
    video_input = _load_video_input(ctx)
    if not video_input.source_url:
        video_input = _resolve_knowledge_source(ctx, video_input)
    ctx.checkpoint()
    output_path = job_dir / "source.mp4"
    legacy_download_video(video_input.source_url, output_path)
