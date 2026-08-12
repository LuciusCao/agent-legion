"""Unit tests for workflow_nodes/video_download.py (download_video node)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from workflow_nodes import video_download
from workspace_libs.cms.client import CmsClientError, CmsVideoLookup
from workspace_libs.node_sdk import AUTH_FAILURE_MARKER_PATH


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
    """An auth-class CMS failure (HTTP 401/403 or a known in-band auth code)
    must record the auth-failure marker (the parent executor invalidates the
    cached connection token) before the error propagates."""
    _write_video_input(tmp_path)
    context = {
        "node_config": {
            "connection": "cms-internal",
            "connection_config": {"token": "conn-token", "api_url": "https://x.example.com"},
        }
    }

    def _lookup(code: str, url: str, token: str) -> CmsVideoLookup:
        raise CmsClientError("CMS 返回错误: code=10015 message=JWT验证失败", auth_failure=True)

    monkeypatch.setattr(video_download, "lookup_knowledge_video", _lookup)

    with pytest.raises(CmsClientError, match="code=10015"):
        video_download.run({}, tmp_path, context)

    marker = tmp_path / AUTH_FAILURE_MARKER_PATH
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8") == "cms-internal"


def test_knowledge_lookup_transport_error_keeps_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transport/non-auth failures (5xx/timeout/DNS, non-auth in-band codes)
    must NOT invalidate the healthy cached token."""
    _write_video_input(tmp_path)
    context = {
        "node_config": {
            "connection": "cms-internal",
            "connection_config": {"token": "conn-token", "api_url": "https://x.example.com"},
        }
    }

    def _lookup(code: str, url: str, token: str) -> CmsVideoLookup:
        raise CmsClientError("CMS request failed: 500 Server Error")

    monkeypatch.setattr(video_download, "lookup_knowledge_video", _lookup)

    with pytest.raises(CmsClientError, match="CMS request failed"):
        video_download.run({}, tmp_path, context)

    assert not (tmp_path / AUTH_FAILURE_MARKER_PATH).exists()
