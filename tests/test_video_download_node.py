"""video_knowledge download node: execution-time CMS resolution.

Knowledge-mode intake writes an opaque ``source_ref``; the download node
resolves it against the CMS through the resource binding + vault chain and
writes the resolved fields back to video_input.json before downloading.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.app.cms.client import CmsVideoLookup
from server.app.services.vault import VaultService
from server.app.services.vault_resources import resource_secret_name
from server.app.workflows.video_knowledge import download_video

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
        "server.app.workflows.video_knowledge.legacy_download_video",
        lambda source_url, output_path: downloaded.append((source_url, output_path)),
    )

    download_video({"id": "j1"}, job_dir, runtime=None)

    assert downloaded == [(url, job_dir / "source.mp4")]


def test_download_knowledge_mode_resolves_via_cms(tmp_path, monkeypatch) -> None:
    job_dir = tmp_path / "job"
    _write_video_input(job_dir)
    settings_config = {
        "resource_providers": {
            "cms.knowledge.video": {
                "resource_key": "knowledge_video",
                "api_url": "http://cms.example/knowledge/detail",
            }
        }
    }
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

    monkeypatch.setattr(
        "server.app.workflows.video_knowledge_source.get_token", lambda env, config: "token"
    )
    monkeypatch.setattr(
        "server.app.workflows.video_knowledge_source.lookup_knowledge_video", fake_lookup
    )
    downloaded = []
    monkeypatch.setattr(
        "server.app.workflows.video_knowledge.legacy_download_video",
        lambda source_url, output_path: downloaded.append(source_url),
    )

    download_video({"id": "j1"}, job_dir, runtime={"settings_config": settings_config})

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
    settings_config = {
        "resource_providers": {
            "cms.knowledge.video": {
                "resource_key": "knowledge_video",
                "api_url": "http://cms.example/knowledge/detail",
            }
        }
    }
    monkeypatch.setattr(
        "server.app.workflows.video_knowledge_source.get_token", lambda env, config: "token"
    )
    monkeypatch.setattr(
        "server.app.workflows.video_knowledge_source.lookup_knowledge_video",
        lambda code, api_url=None, token=None: CmsVideoLookup(status="not_found"),
    )

    with pytest.raises(RuntimeError, match="K404"):
        download_video({"id": "j1"}, job_dir, runtime={"settings_config": settings_config})


def test_download_knowledge_mode_empty_source_ref_fails(tmp_path) -> None:
    job_dir = tmp_path / "job"
    _write_video_input(job_dir, source_ref="")

    with pytest.raises(RuntimeError, match="source_ref"):
        download_video({"id": "j1"}, job_dir, runtime=None)


@pytest.fixture
def vault_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", key)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    return key


def test_download_knowledge_mode_resolves_vault_secret_ref(
    tmp_path, monkeypatch, job_db, settings, vault_key
) -> None:
    """The binding stores only a secret_ref; the node resolves the plaintext
    token in memory and passes it to the CMS lookup."""
    job_dir = tmp_path / "job"
    _write_video_input(job_dir)
    workspace = job_db.create_workspace("vault-knowledge-video")
    name = resource_secret_name("knowledge_video", "token")
    VaultService(job_db.path, settings.config).set(workspace["id"], name, PLAINTEXT)
    job_db.update_workspace(
        workspace["id"],
        resource_config={
            "resources": {
                "knowledge_video": {
                    "enabled": True,
                    "config": {
                        "api_url": "http://cms.example/knowledge/detail",
                        "token": {"secret_ref": name},
                    },
                }
            }
        },
    )
    calls = []

    def fake_lookup(code, api_url=None, token=None):
        calls.append((code, api_url, token))
        return CmsVideoLookup(
            status="found",
            url="https://example.invalid/resolved.mp4",
            title="CMS Title",
            source_uuid="uuid-1",
        )

    monkeypatch.setattr(
        "server.app.workflows.video_knowledge_source.lookup_knowledge_video", fake_lookup
    )
    monkeypatch.setattr(
        "server.app.workflows.video_knowledge.legacy_download_video",
        lambda source_url, output_path: None,
    )

    job = {"id": "j1", "workspace_id": str(workspace["id"]), "batch_id": ""}
    context = {"settings_config": settings.config, "job_db": job_db}
    download_video(job, job_dir, runtime=context)

    assert len(calls) == 1
    code, api_url, token = calls[0]
    assert code == "K001"
    assert api_url.startswith("http://cms.example/knowledge/detail")
    assert token == PLAINTEXT
    # Only the secret_ref may touch disk; the plaintext stays in memory.
    assert PLAINTEXT not in (job_dir / "video_input.json").read_text(encoding="utf-8")
