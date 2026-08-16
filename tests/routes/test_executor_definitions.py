"""DB-backed executor definition catalog routes (schema v30)."""

from __future__ import annotations

import pytest


@pytest.fixture
def client(client_factory):
    """Private app per test: publish/rollback/archive hot-reload the running
    executor registry and settings.executor_definitions in-process, which must
    not leak into the worker-session shared app used by the default client
    fixture."""
    with client_factory(fresh=True) as c:
        yield c


BASE = "/api/executor-definitions"
PAYLOAD_V1 = {
    "kind": "code",
    "global_capacity": 2,
    "capabilities": {"clean_items": {}},
}
PAYLOAD_V2 = {
    "kind": "code",
    "global_capacity": 4,
    "capabilities": {"clean_items": {}},
}


def test_list_contains_seeded_builtin(client) -> None:
    listed = client.get(BASE)
    assert listed.status_code == 200
    executors = {item["executor_id"]: item for item in listed.json()["executors"]}
    seeded = executors["code-default"]
    assert seeded["kind"] == "code"
    assert seeded["global_capacity"] == 16
    assert seeded["status"] == "published"
    assert "intake_knowledge_points" in seeded["capabilities"]
    assert len(seeded["capabilities"]) == 2


def test_create_draft_publish_flow(client) -> None:
    created = client.post(BASE, json={"executor_id": "code-extra", **PAYLOAD_V1})
    assert created.status_code == 200
    body = created.json()
    assert body["executor_id"] == "code-extra"
    assert body["version"] == 1
    assert body["status"] == "draft"
    assert body["created_by"].startswith("user:")
    assert body["definition"]["global_capacity"] == 2

    detail = client.get(f"{BASE}/code-extra")
    assert detail.status_code == 200
    assert detail.json()["latest"]["status"] == "draft"
    assert detail.json()["published"] is None

    published = client.post(f"{BASE}/code-extra/publish")
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["published_at"] is not None

    detail = client.get(f"{BASE}/code-extra").json()
    assert detail["published"]["version"] == 1


def test_list_shows_latest_per_executor(client) -> None:
    client.post(BASE, json={"executor_id": "code-extra", **PAYLOAD_V1})
    client.post(f"{BASE}/code-extra/publish")
    client.put(f"{BASE}/code-extra/draft", json=PAYLOAD_V2)

    listed = client.get(BASE)
    assert listed.status_code == 200
    executors = {item["executor_id"]: item for item in listed.json()["executors"]}
    assert executors["code-extra"]["status"] == "draft"
    assert executors["code-extra"]["has_draft"] is True
    assert executors["code-extra"]["version"] == 2
    assert executors["code-extra"]["global_capacity"] == 4


def test_versions_and_rollback(client) -> None:
    client.post(BASE, json={"executor_id": "code-extra", **PAYLOAD_V1})
    client.post(f"{BASE}/code-extra/publish")
    client.put(f"{BASE}/code-extra/draft", json=PAYLOAD_V2)
    client.post(f"{BASE}/code-extra/publish")

    versions = client.get(f"{BASE}/code-extra/versions").json()["versions"]
    assert [row["version"] for row in versions] == [2, 1]
    assert {row["version"]: row["status"] for row in versions} == {
        1: "archived",
        2: "published",
    }
    # The list stays lean: no definition payload in version summaries.
    assert "definition" not in versions[0]

    rolled = client.post(f"{BASE}/code-extra/rollback", json={"version": 1})
    assert rolled.status_code == 200
    assert rolled.json()["version"] == 3
    assert rolled.json()["status"] == "published"
    assert rolled.json()["definition"]["global_capacity"] == 2


def test_copy_creates_draft(client) -> None:
    client.post(BASE, json={"executor_id": "code-extra", **PAYLOAD_V1})
    client.post(f"{BASE}/code-extra/publish")

    copied = client.post(f"{BASE}/code-extra/copy", json={"new_executor_id": "code-fork"})
    assert copied.status_code == 200
    body = copied.json()
    assert body["executor_id"] == "code-fork"
    assert body["version"] == 1
    assert body["status"] == "draft"

    missing = client.post(f"{BASE}/code-missing/copy", json={"new_executor_id": "code-fork2"})
    assert missing.status_code == 404


def test_archive_all(client) -> None:
    client.post(BASE, json={"executor_id": "code-extra", **PAYLOAD_V1})
    client.post(f"{BASE}/code-extra/publish")

    archived = client.delete(f"{BASE}/code-extra")
    assert archived.status_code == 200
    assert archived.json()["archived"] == 1

    detail = client.get(f"{BASE}/code-extra").json()
    assert detail["published"] is None
    assert detail["latest"]["status"] == "archived"


def test_unknown_executor_404(client) -> None:
    assert client.get(f"{BASE}/code-missing").status_code == 404
    assert client.get(f"{BASE}/code-missing/versions").status_code == 404
    assert client.post(f"{BASE}/code-missing/publish").status_code == 404


def test_invalid_definition_rejected(client) -> None:
    bad_schema = client.post(
        BASE,
        json={
            "executor_id": "code-bad",
            **PAYLOAD_V1,
            "capabilities": {
                "x": {
                    "path": "workflow_nodes/example_publish.py",
                    "config_schema": {"type": "object", "properties": {"bad": {"type": "nope"}}},
                }
            },
        },
    )
    assert bad_schema.status_code == 422
    unknown_kind = client.post(
        BASE, json={"executor_id": "code-bad", **PAYLOAD_V1, "kind": "quantum"}
    )
    assert unknown_kind.status_code == 422


def test_legacy_path_key_is_stripped_at_load(client) -> None:
    """Pre-#96 stored definitions may still carry ``path``: tolerated and
    dropped (the capability becomes custom-code-only), never resurrected."""
    payload = {
        "kind": "code",
        "global_capacity": 1,
        "capabilities": {"x": {"path": "workflow_nodes/does_not_exist.py"}},
    }
    assert client.post(BASE, json={"executor_id": "code-legacy", **payload}).status_code == 200
    assert client.post(f"{BASE}/code-legacy/publish").status_code == 200

    catalog = client.get("/api/executors")
    assert catalog.status_code == 200
    executors = {e["id"]: e for e in catalog.json()["executors"]}
    details = executors["code-legacy"]["capability_details"]
    assert len(details) == 1
    assert details[0]["name"] == "x"
    assert "path" not in details[0]


def test_endpoints_require_auth(anon_client) -> None:
    assert anon_client.get(BASE).status_code == 401
    assert anon_client.post(BASE, json={"executor_id": "x", **PAYLOAD_V1}).status_code == 401
    assert anon_client.put(f"{BASE}/code-default/draft", json=PAYLOAD_V1).status_code == 401
    assert anon_client.post(f"{BASE}/code-default/publish").status_code == 401
    assert anon_client.delete(f"{BASE}/code-default").status_code == 401


def test_publish_hot_reloads_runtime_registry(client) -> None:
    """Publish takes effect without a restart: the running registry swaps."""
    registry = client.app.state.executor_registry
    assert registry.get("code-extra") is None

    client.post(BASE, json={"executor_id": "code-extra", **PAYLOAD_V1})
    published = client.post(f"{BASE}/code-extra/publish")
    assert published.status_code == 200

    executor = registry.require("code-extra", "clean_items")
    assert executor.supports("clean_items")
    assert registry.global_capacity("code-extra") == 2
    settings_definitions = client.app.state.settings.executor_definitions
    assert settings_definitions["code-extra"].global_capacity == 2


def test_rollback_and_archive_hot_reload_runtime_registry(client) -> None:
    client.post(BASE, json={"executor_id": "code-extra", **PAYLOAD_V1})
    client.post(f"{BASE}/code-extra/publish")
    client.put(f"{BASE}/code-extra/draft", json=PAYLOAD_V2)
    client.post(f"{BASE}/code-extra/publish")
    registry = client.app.state.executor_registry
    assert registry.global_capacity("code-extra") == 4

    rolled = client.post(f"{BASE}/code-extra/rollback", json={"version": 1})
    assert rolled.status_code == 200
    assert registry.global_capacity("code-extra") == 2

    archived = client.delete(f"{BASE}/code-extra")
    assert archived.status_code == 200
    assert registry.get("code-extra") is None
    assert "code-extra" not in client.app.state.settings.executor_definitions
