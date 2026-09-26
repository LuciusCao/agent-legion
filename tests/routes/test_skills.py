"""Skill validation routes (Studio Agent editor)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import server.app.routes.skills as skills_routes


@pytest.fixture
def skills_base(tmp_path, monkeypatch):
    base = tmp_path / "skills"
    skill_dir = base / "wf" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    monkeypatch.setattr(
        skills_routes,
        "build_skill_manager",
        lambda _dsn, _runs_dir=None: SimpleNamespace(base_dir=base, load_lock=lambda: None),
    )
    return base


def test_validate_endpoint(skills_base, client) -> None:
    response = client.post(
        "/api/skills/validate",
        params={"workspace_id": "wf"},
        json={"path": str(skills_base / "wf" / "review")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["skill_key"] == "wf/review"
    assert body["tags"] == []
    assert body["latest_tag"] is None


def test_validate_endpoint_rejects_invalid_path(skills_base, client) -> None:
    response = client.post(
        "/api/skills/validate", params={"workspace_id": "wf"}, json={"path": "/etc"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["error"]


def test_tags_endpoint(skills_base, client) -> None:
    response = client.get(
        "/api/skills/tags",
        params={"path": str(skills_base / "wf" / "review"), "workspace_id": "wf"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tags"] == []
    assert body["latest_tag"] is None


def test_endpoints_require_auth(skills_base, anon_client) -> None:
    assert anon_client.post("/api/skills/validate", json={"path": "/tmp"}).status_code == 401
    assert anon_client.get("/api/skills/tags", params={"path": "/tmp"}).status_code == 401


def test_endpoints_work_through_symlinked_skills_root(tmp_path, monkeypatch, client) -> None:
    """codex P2 on #753: when the skills root (or an ancestor like ~/.agents)
    is a symlink, the validator must derive relative paths against the
    physically-resolved base — a spelling-based relative_to() raised
    ValueError and turned valid reads into 500s."""
    real_base = tmp_path / "real-home" / ".agents" / "skills"
    skill_dir = real_base / "wf" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    # ~/.agents is a symlink → the base keeps its spelling while filesystem
    # operations resolve through the link.
    link_home = tmp_path / "home"
    (tmp_path / "real-home").mkdir(exist_ok=True)
    link_home.mkdir()
    (link_home / ".agents").symlink_to(real_base.parent, target_is_directory=True)
    monkeypatch.setenv("HOME", str(link_home))

    response = client.get(
        "/api/skills/tags",
        params={
            "path": str(link_home / ".agents" / "skills" / "wf" / "review"),
            "workspace_id": "wf",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["path"] == "wf/review"
