"""DB-backed Agent definition catalog routes (schema v26)."""

from __future__ import annotations

BASE = "/api/agent-definitions"
PAYLOAD_V1 = {
    "capability": "review_keywords",
    "runtime": "velites",
    "skill": "question_comprehension_info/review_key_info",
}
PAYLOAD_V2 = {
    "capability": "review_keywords",
    "runtime": "velites",
    "skill": "question_comprehension_info/review_key_info",
    "tools": ["read"],
}


def test_create_draft_publish_flow(client) -> None:
    created = client.post(BASE, json={"agent_id": "agent-a", **PAYLOAD_V1})
    assert created.status_code == 200
    body = created.json()
    assert body["agent_id"] == "agent-a"
    assert body["version"] == 1
    assert body["status"] == "draft"
    assert body["created_by"].startswith("user:")
    assert body["definition"]["capability"] == "review_keywords"

    detail = client.get(f"{BASE}/agent-a")
    assert detail.status_code == 200
    assert detail.json()["latest"]["status"] == "draft"
    assert detail.json()["published"] is None

    published = client.post(f"{BASE}/agent-a/publish")
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["published_at"] is not None

    detail = client.get(f"{BASE}/agent-a").json()
    assert detail["published"]["version"] == 1


def test_list_shows_latest_per_agent(client) -> None:
    client.post(BASE, json={"agent_id": "agent-a", **PAYLOAD_V1})
    client.post(f"{BASE}/agent-a/publish")
    client.put(f"{BASE}/agent-a/draft", json=PAYLOAD_V2)

    listed = client.get(BASE)
    assert listed.status_code == 200
    agents = {item["agent_id"]: item for item in listed.json()["agents"]}
    assert agents["agent-a"]["status"] == "draft"
    assert agents["agent-a"]["has_draft"] is True
    assert agents["agent-a"]["version"] == 2
    assert agents["agent-a"]["capability"] == "review_keywords"


def test_versions_and_rollback(client) -> None:
    client.post(BASE, json={"agent_id": "agent-a", **PAYLOAD_V1})
    client.post(f"{BASE}/agent-a/publish")
    client.put(f"{BASE}/agent-a/draft", json=PAYLOAD_V2)
    client.post(f"{BASE}/agent-a/publish")

    versions = client.get(f"{BASE}/agent-a/versions").json()["versions"]
    assert [row["version"] for row in versions] == [2, 1]
    assert {row["version"]: row["status"] for row in versions} == {
        1: "archived",
        2: "published",
    }
    # The list stays lean: no definition payload in version summaries.
    assert "definition" not in versions[0]

    rolled = client.post(f"{BASE}/agent-a/rollback", json={"version": 1})
    assert rolled.status_code == 200
    assert rolled.json()["version"] == 3
    assert rolled.json()["status"] == "published"
    assert rolled.json()["definition"]["tools"] == ["read", "write", "bash"]


def test_publish_rejects_duplicate_capability(client) -> None:
    client.post(BASE, json={"agent_id": "agent-a", **PAYLOAD_V1})
    client.post(f"{BASE}/agent-a/publish")
    client.post(BASE, json={"agent_id": "agent-b", **PAYLOAD_V1})

    conflict = client.post(f"{BASE}/agent-b/publish")
    assert conflict.status_code == 409


def test_copy_creates_draft(client) -> None:
    client.post(BASE, json={"agent_id": "agent-a", **PAYLOAD_V1})
    client.post(f"{BASE}/agent-a/publish")

    copied = client.post(f"{BASE}/agent-a/copy", json={"new_agent_id": "agent-b"})
    assert copied.status_code == 200
    body = copied.json()
    assert body["agent_id"] == "agent-b"
    assert body["version"] == 1
    assert body["status"] == "draft"

    missing = client.post(f"{BASE}/agent-missing/copy", json={"new_agent_id": "agent-c"})
    assert missing.status_code == 404


def test_archive_all(client) -> None:
    client.post(BASE, json={"agent_id": "agent-a", **PAYLOAD_V1})
    client.post(f"{BASE}/agent-a/publish")

    archived = client.delete(f"{BASE}/agent-a")
    assert archived.status_code == 200
    assert archived.json()["archived"] == 1

    detail = client.get(f"{BASE}/agent-a").json()
    assert detail["published"] is None
    assert detail["latest"]["status"] == "archived"


def test_unknown_agent_404(client) -> None:
    assert client.get(f"{BASE}/agent-missing").status_code == 404
    assert client.get(f"{BASE}/agent-missing/versions").status_code == 404
    assert client.post(f"{BASE}/agent-missing/publish").status_code == 404


def test_invalid_definition_rejected(client) -> None:
    absolute_skill = client.post(
        BASE, json={"agent_id": "agent-a", **PAYLOAD_V1, "skill": "/etc/passwd"}
    )
    assert absolute_skill.status_code == 422
    bad_schema = client.post(
        BASE,
        json={"agent_id": "agent-a", **PAYLOAD_V1, "config_schema": {"type": "nope"}},
    )
    assert bad_schema.status_code == 422
