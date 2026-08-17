"""DB-backed Agent definition catalog routes (workspace-scoped, schema v46)."""

from __future__ import annotations

import pytest

BASE = "/api/agent-definitions"
PAYLOAD_V1 = {
    "capability": "review_keywords",
    "runtime": "velites",
    "skill": "demo_workflow/review_key_info",
}
PAYLOAD_V2 = {
    "capability": "review_keywords",
    "runtime": "velites",
    "skill": "demo_workflow/review_key_info",
    "tools": ["read"],
}


@pytest.fixture
def workspace_id(job_db) -> str:
    return job_db.create_workspace("Agent Routes WS", default_workflow_key="demo_workflow")["id"]


@pytest.fixture
def ws(workspace_id) -> dict[str, str]:
    """Every catalog endpoint takes the required workspace_id query parameter."""
    return {"workspace_id": workspace_id}


def test_create_draft_publish_flow(client, ws) -> None:
    created = client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
    assert created.status_code == 200
    body = created.json()
    assert body["agent_id"] == "agent-a"
    assert body["version"] == 1
    assert body["status"] == "draft"
    assert body["created_by"].startswith("user:")
    assert body["definition"]["capability"] == "review_keywords"

    detail = client.get(f"{BASE}/agent-a", params=ws)
    assert detail.status_code == 200
    assert detail.json()["latest"]["status"] == "draft"
    assert detail.json()["published"] is None

    published = client.post(f"{BASE}/agent-a/publish", params=ws)
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["published_at"] is not None

    detail = client.get(f"{BASE}/agent-a", params=ws).json()
    assert detail["published"]["version"] == 1


def test_workspace_id_required(client) -> None:
    assert client.get(BASE).status_code == 422
    assert client.post(BASE, json={"agent_id": "agent-a", **PAYLOAD_V1}).status_code == 422


def test_catalogs_are_workspace_isolated(client, job_db, ws, workspace_id) -> None:
    client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
    client.post(f"{BASE}/agent-a/publish", params=ws)

    other = job_db.create_workspace("Other WS", default_workflow_key="demo_workflow")["id"]
    listed = client.get(BASE, params={"workspace_id": other})
    assert listed.status_code == 200
    assert listed.json()["agents"] == []
    assert client.get(f"{BASE}/agent-a", params={"workspace_id": other}).status_code == 404


def test_list_shows_latest_per_agent(client, ws) -> None:
    client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
    client.post(f"{BASE}/agent-a/publish", params=ws)
    client.put(f"{BASE}/agent-a/draft", params=ws, json=PAYLOAD_V2)

    listed = client.get(BASE, params=ws)
    assert listed.status_code == 200
    agents = {item["agent_id"]: item for item in listed.json()["agents"]}
    assert agents["agent-a"]["status"] == "draft"
    assert agents["agent-a"]["has_draft"] is True
    assert agents["agent-a"]["version"] == 2
    assert agents["agent-a"]["capability"] == "review_keywords"


def test_versions_and_rollback(client, ws) -> None:
    client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
    client.post(f"{BASE}/agent-a/publish", params=ws)
    client.put(f"{BASE}/agent-a/draft", params=ws, json=PAYLOAD_V2)
    client.post(f"{BASE}/agent-a/publish", params=ws)

    versions = client.get(f"{BASE}/agent-a/versions", params=ws).json()["versions"]
    assert [row["version"] for row in versions] == [2, 1]
    assert {row["version"]: row["status"] for row in versions} == {
        1: "archived",
        2: "published",
    }
    # The list stays lean: no definition payload in version summaries.
    assert "definition" not in versions[0]

    rolled = client.post(f"{BASE}/agent-a/rollback", params=ws, json={"version": 1})
    assert rolled.status_code == 200
    assert rolled.json()["version"] == 3
    assert rolled.json()["status"] == "published"
    assert rolled.json()["definition"]["tools"] == ["read", "write", "bash"]


def test_publish_rejects_duplicate_capability(client, ws) -> None:
    client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
    client.post(f"{BASE}/agent-a/publish", params=ws)
    client.post(BASE, params=ws, json={"agent_id": "agent-b", **PAYLOAD_V1})

    conflict = client.post(f"{BASE}/agent-b/publish", params=ws)
    assert conflict.status_code == 409


def test_copy_creates_draft(client, ws) -> None:
    client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
    client.post(f"{BASE}/agent-a/publish", params=ws)

    copied = client.post(f"{BASE}/agent-a/copy", params=ws, json={"new_agent_id": "agent-b"})
    assert copied.status_code == 200
    body = copied.json()
    assert body["agent_id"] == "agent-b"
    assert body["version"] == 1
    assert body["status"] == "draft"

    missing = client.post(f"{BASE}/agent-missing/copy", params=ws, json={"new_agent_id": "agent-c"})
    assert missing.status_code == 404


def test_archive_all(client, ws) -> None:
    client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
    client.post(f"{BASE}/agent-a/publish", params=ws)

    archived = client.delete(f"{BASE}/agent-a", params=ws)
    assert archived.status_code == 200
    assert archived.json()["archived"] == 1

    detail = client.get(f"{BASE}/agent-a", params=ws).json()
    assert detail["published"] is None
    assert detail["latest"]["status"] == "archived"


def test_unknown_agent_404(client, ws) -> None:
    assert client.get(f"{BASE}/agent-missing", params=ws).status_code == 404
    assert client.get(f"{BASE}/agent-missing/versions", params=ws).status_code == 404
    assert client.post(f"{BASE}/agent-missing/publish", params=ws).status_code == 404


def test_invalid_definition_rejected(client, ws) -> None:
    absolute_skill = client.post(
        BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1, "skill": "/etc/passwd"}
    )
    assert absolute_skill.status_code == 422
    bad_schema = client.post(
        BASE,
        params=ws,
        json={"agent_id": "agent-a", **PAYLOAD_V1, "config_schema": {"type": "nope"}},
    )
    assert bad_schema.status_code == 422
