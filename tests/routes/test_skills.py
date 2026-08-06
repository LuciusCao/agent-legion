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
        lambda _root: SimpleNamespace(base_dir=base, lock_path=None),
    )
    return base


def test_validate_endpoint(skills_base, client) -> None:
    response = client.post(
        "/api/skills/validate", json={"path": str(skills_base / "wf" / "review")}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["skill_key"] == "wf/review"
    assert body["tags"] == []
    assert body["latest_tag"] is None


def test_validate_endpoint_rejects_invalid_path(skills_base, client) -> None:
    response = client.post("/api/skills/validate", json={"path": "/etc"})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["error"]


def test_tags_endpoint(skills_base, client) -> None:
    response = client.get("/api/skills/tags", params={"path": str(skills_base / "wf" / "review")})

    assert response.status_code == 200
    body = response.json()
    assert body["tags"] == []
    assert body["latest_tag"] is None


def test_endpoints_require_auth(skills_base, anon_client) -> None:
    assert anon_client.post("/api/skills/validate", json={"path": "/tmp"}).status_code == 401
    assert anon_client.get("/api/skills/tags", params={"path": "/tmp"}).status_code == 401
