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

import json
import logging
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from server.app.pipeline.download import download_video as legacy_download_video
from server.app.services.connection_tokens import report_node_auth_failure
from server.app.video_capabilities.contracts import VideoKnowledgeInput
from workspace_libs.cms import urls as cms_urls
from workspace_libs.cms.client import CmsClientError, get_token
from workspace_libs.cms.knowledge import lookup_knowledge_video

logger = logging.getLogger(__name__)


def _load_video_input(job_dir: Path) -> VideoKnowledgeInput:
    raw = json.loads((job_dir / "video_input.json").read_text(encoding="utf-8"))
    return VideoKnowledgeInput.from_mapping(raw)


# Retired node-level connection keys (pre-connection era). They are honored
# only when no connection was injected (legacy frozen payloads); a resolved
# connection always wins.
_LEGACY_CONNECTION_KEYS = ("token", "env", "base_url", "api_url", "knowledge_url")


def _cms_config(context: dict[str, Any]) -> dict[str, Any]:
    """Effective CMS config: dispatch-injected connection + node overrides.

    The ``connection_config`` block arrives resolved from the instance-level
    external connection at dispatch time (base URL/endpoint config plus the
    plaintext token, in memory only). Node/workspace business overrides win.
    Legacy frozen payloads without a connection fall back to their
    vault-resolved node ``token``.
    """
    merged: dict[str, Any] = {}
    node_config = context.get("node_config")
    # The dispatch layer injects the resolved connection into the node config
    # (ExecutionContext.node_config → runtime["node_config"]): non-secret
    # endpoint config plus the plaintext token, in memory only.
    injected = node_config.get("connection_config") if isinstance(node_config, dict) else None
    has_connection = isinstance(injected, dict) and bool(injected)
    if isinstance(injected, dict) and injected:
        merged.update({key: value for key, value in injected.items() if value not in (None, "")})
    if isinstance(node_config, dict):
        for key, value in node_config.items():
            if key in ("connection", "connection_config") or value in (None, ""):
                continue
            if has_connection and key in _LEGACY_CONNECTION_KEYS:
                continue
            merged[key] = value
    return merged


def _resolve_knowledge_source(
    job: dict[str, Any],
    job_dir: Path,
    video_input: VideoKnowledgeInput,
    context: dict[str, Any],
) -> VideoKnowledgeInput:
    """Resolve an opaque knowledge ``source_ref`` against the CMS."""
    if not video_input.source_ref:
        raise RuntimeError("video source_url is empty and no source_ref is set")
    cms_config = _cms_config(context)
    api_url = str(
        cms_config.get("api_url")
        or cms_config.get("knowledge_url")
        or cms_urls.knowledge_url(cms_config)
    )
    token = get_token(str(cms_config.get("env", "")), cms_config)
    try:
        lookup = lookup_knowledge_video(video_input.source_ref, api_url, token)
    except CmsClientError:
        # Auth-class failure: invalidate the cached connection token so the
        # next dispatch re-acquires instead of reusing a dead one.
        report_node_auth_failure(context)
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
