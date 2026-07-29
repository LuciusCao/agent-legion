"""video_knowledge download node: execution-time CMS resolution.

Knowledge-mode intake writes an opaque ``source_ref``; the download node
(``workflow_nodes/video_download.py``) resolves it against the CMS through
the node config chain (global ``cms:`` defaults overridden by
``runtime["node_config"]``; the dispatch layer already resolved any vault
secret_ref into a plaintext token) and writes the resolved fields back to
video_input.json before downloading.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.app.cms.client import CmsVideoLookup
from workflow_nodes.video_download import run as download_video

PLAINTEXT = "knowledge-video-cms-token"


def _write_video_input(job_dir: Path, **overrides) -> None:
    payload = {
        "schema_version": 1,
        "entity_type": "video",
        "content_type": "knowledge",
        "legacy_video_id": "",
        "external_id": "K001",
        "source_uuid": "",
        "source_url": "",
        "source_ref": "K001",
        "title": "Video K001",
    }
    payload.update(overrides)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "video_input.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _read_video_input(job_dir: Path) -> dict:
    return json.loads((job_dir / "video_input.json").read_text(encoding="utf-8"))


def test_download_urls_mode_downloads_source_url_directly(tmp_path, monkeypatch) -> None:
    """Regression: urls intake mode keeps the pre-resolved source_url path."""
    job_dir = tmp_path / "job"
    url = "https://example.invalid/video.mp4"
    _write_video_input(job_dir, source_url=url, external_id=url, source_ref="")

    def fail_on_cms(*args, **kwargs):
        raise AssertionError("urls mode must not call the CMS")

    monkeypatch.setattr("server.app.cms.knowledge.lookup_knowledge_video", fail_on_cms)
    downloaded = []
    monkeypatch.setattr(
        "workflow_nodes.video_download.legacy_download_video",
        lambda source_url, output_path: downloaded.append((source_url, output_path)),
    )

    download_video({"id": "j1"}, job_dir, runtime=None)

    assert downloaded == [(url, job_dir / "source.mp4")]


def test_download_knowledge_mode_resolves_via_cms(tmp_path, monkeypatch) -> None:
    job_dir = tmp_path / "job"
    _write_video_input(job_dir)
    node_config = {"api_url": "http://cms.example/knowledge/detail"}
    lookup = CmsVideoLookup(
        status="found",
        url="https://example.invalid/resolved.mp4",
        title="CMS Title",
        source_uuid="uuid-1",
    )
    calls = []

    def fake_lookup(code, api_url=None, token=None):
        calls.append((code, api_url, token))
        return lookup

    monkeypatch.setattr("workflow_nodes.video_download.get_token", lambda env, config: "token")
    monkeypatch.setattr("workflow_nodes.video_download.lookup_knowledge_video", fake_lookup)
    downloaded = []
    monkeypatch.setattr(
        "workflow_nodes.video_download.legacy_download_video",
        lambda source_url, output_path: downloaded.append(source_url),
    )

    download_video({"id": "j1"}, job_dir, runtime={"node_config": node_config})

    assert calls == [("K001", "http://cms.example/knowledge/detail", "token")]
    assert downloaded == ["https://example.invalid/resolved.mp4"]
    rewritten = _read_video_input(job_dir)
    assert rewritten["source_url"] == "https://example.invalid/resolved.mp4"
    assert rewritten["source_uuid"] == "uuid-1"
    assert rewritten["title"] == "CMS Title"
    assert rewritten["source_ref"] == "K001"


def test_download_knowledge_mode_not_found_fails_with_code(tmp_path, monkeypatch) -> None:
    job_dir = tmp_path / "job"
    _write_video_input(job_dir, source_ref="K404")
    node_config = {"api_url": "http://cms.example/knowledge/detail"}
    monkeypatch.setattr("workflow_nodes.video_download.get_token", lambda env, config: "token")
    monkeypatch.setattr(
        "workflow_nodes.video_download.lookup_knowledge_video",
        lambda code, api_url=None, token=None: CmsVideoLookup(status="not_found"),
    )

    with pytest.raises(RuntimeError, match="K404"):
        download_video({"id": "j1"}, job_dir, runtime={"node_config": node_config})


def test_download_knowledge_mode_empty_source_ref_fails(tmp_path) -> None:
    job_dir = tmp_path / "job"
    _write_video_input(job_dir, source_ref="")

    with pytest.raises(RuntimeError, match="source_ref"):
        download_video({"id": "j1"}, job_dir, runtime=None)


def test_download_knowledge_mode_uses_node_config_token(tmp_path, monkeypatch) -> None:
    """The dispatch layer resolves the vault secret_ref in memory and injects
    the plaintext token via runtime["node_config"]; the node passes it to the
    CMS lookup without persisting it."""
    job_dir = tmp_path / "job"
    _write_video_input(job_dir)
    node_config = {
        "api_url": "http://cms.example/knowledge/detail",
        "token": PLAINTEXT,
    }
    calls = []

    def fake_lookup(code, api_url=None, token=None):
        calls.append((code, api_url, token))
        return CmsVideoLookup(
            status="found",
            url="https://example.invalid/resolved.mp4",
            title="CMS Title",
            source_uuid="uuid-1",
        )

    monkeypatch.setattr("workflow_nodes.video_download.lookup_knowledge_video", fake_lookup)
    monkeypatch.setattr(
        "workflow_nodes.video_download.legacy_download_video",
        lambda source_url, output_path: None,
    )

    download_video(
        {"id": "j1", "workspace_id": "ws-a"}, job_dir, runtime={"node_config": node_config}
    )

    assert len(calls) == 1
    code, api_url, token = calls[0]
    assert code == "K001"
    assert api_url.startswith("http://cms.example/knowledge/detail")
    assert token == PLAINTEXT
    # The plaintext stays in memory; it is never written to the job dir.
    assert PLAINTEXT not in (job_dir / "video_input.json").read_text(encoding="utf-8")
