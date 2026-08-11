"""Unit tests for workflow_nodes/video_download.py (download_video node)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from workflow_nodes import video_download
from workspace_libs.cms.client import CmsClientError, CmsVideoLookup


def _write_video_input(job_dir: Path, **overrides: Any) -> None:
    payload = {
        "schema_version": 1,
        "entity_type": "video",
        "content_type": "knowledge",
        "legacy_video_id": "v-1",
        "external_id": "v-1",
        "source_uuid": "",
        "source_url": "",
        "source_ref": "ref-1",
        "title": "T",
    }
    payload.update(overrides)
    (job_dir / "video_input.json").write_text(json.dumps(payload), encoding="utf-8")


def test_knowledge_lookup_prefers_injected_connection_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dispatch-injected connection supplies the token and endpoint; legacy
    node-level connection keys are ignored while a connection is present."""
    _write_video_input(tmp_path)
    context = {
        "node_config": {
            "connection": "cms-internal",
            "connection_config": {
                "token": "conn-token",
                "api_url": "https://conn.example.com/knowledge",
            },
            "token": "legacy-token",
            "api_url": "https://legacy.example.com/knowledge",
        }
    }
    captured: dict[str, Any] = {}

    def _lookup(code: str, url: str, token: str) -> CmsVideoLookup:
        captured["code"] = code
        captured["url"] = url
        captured["token"] = token
        return CmsVideoLookup(
            status="ok", url="https://cdn.example.com/v.mp4", title="T", source_uuid="u-1"
        )

    monkeypatch.setattr(video_download, "lookup_knowledge_video", _lookup)
    monkeypatch.setattr(video_download, "legacy_download_video", lambda url, path: None)

    video_download.run({}, tmp_path, context)

    # Real workspace_libs get_token reads config["token"]: the injected
    # connection token wins over the legacy node token.
    assert captured == {
        "code": "ref-1",
        "url": "https://conn.example.com/knowledge",
        "token": "conn-token",
    }


def test_knowledge_lookup_cms_error_reports_auth_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An auth-class CMS failure must invalidate the cached connection token
    via report_node_auth_failure before the error propagates."""
    _write_video_input(tmp_path)
    context = {
        "node_config": {
            "connection": "cms-internal",
            "connection_config": {"token": "conn-token", "api_url": "https://x.example.com"},
        }
    }

    def _lookup(code: str, url: str, token: str) -> CmsVideoLookup:
        raise CmsClientError("auth failed")

    monkeypatch.setattr(video_download, "lookup_knowledge_video", _lookup)
    reported: list[dict[str, Any]] = []
    monkeypatch.setattr(
        video_download, "report_node_auth_failure", lambda ctx: reported.append(ctx)
    )

    with pytest.raises(CmsClientError, match="auth failed"):
        video_download.run({}, tmp_path, context)

    assert reported == [context]
