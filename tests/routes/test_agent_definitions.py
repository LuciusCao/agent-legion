"""Read-only historical Agent definition routes (workspace-scoped, schema v46)."""

from __future__ import annotations

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.services.versioned_entities import VersionedEntityStore
from tests.helpers import publish_agent_definition, save_agent_definition_draft

BASE = "/api/agent-definitions"
DEFINITION_V1 = AgentDefinition(
    capability="review_keywords", runtime="velites", skill="demo_workflow/review_key_info"
)
DEFINITION_V2 = AgentDefinition(
    capability="renamed_cap",
    runtime="velites",
    skill="demo_workflow/review_key_info",
    tools=("read",),
)


@pytest.fixture
def workspace_id(job_db) -> str:
    return job_db.create_workspace("Agent Routes WS")["id"]


@pytest.fixture
def ws(workspace_id) -> dict[str, str]:
    return {"workspace_id": workspace_id}


def _save(job_db, workspace_id: str, agent_id: str, definition: AgentDefinition):
    return save_agent_definition_draft(
        job_db.dsn_identity, workspace_id, agent_id, definition, created_by="test-seed"
    )


def _publish_saved(job_db, workspace_id: str, agent_id: str, saved) -> None:
    publish_agent_definition(job_db.dsn_identity, workspace_id, agent_id, saved.definition_hash)


def _agent_rows(job_db, workspace_id: str) -> list[dict]:
    with job_db.read() as conn:
        rows = conn.execute(
            "select id, entity_key, version, status, definition_json, definition_hash,"
            " created_by, created_at, published_at from versioned_entities"
            " where entity_type='agent' and workspace_id=%s order by id",
            (workspace_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def test_reads_preserve_latest_published_and_version_history(
    client, job_db, ws, workspace_id
) -> None:
    published = _save(job_db, workspace_id, "agent-a", DEFINITION_V1)
    _publish_saved(job_db, workspace_id, "agent-a", published)
    _save(job_db, workspace_id, "agent-a", DEFINITION_V2)

    listed = client.get(BASE, params=ws)
    assert listed.status_code == 200, listed.text
    assert listed.json()["agents"] == [
        {
            "agent_id": "agent-a",
            "capability": "renamed_cap",
            "runtime": "velites",
            "skill": "demo_workflow/review_key_info",
            "version": 2,
            "status": "draft",
            "has_draft": True,
            "published_at": None,
            "published_capability": "review_keywords",
            "published_version": 1,
        }
    ]

    detail = client.get(f"{BASE}/agent-a", params=ws)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["latest"]["version"] == 2
    assert body["latest"]["status"] == "draft"
    assert body["latest"]["definition"]["capability"] == "renamed_cap"
    assert body["published"]["version"] == 1
    assert body["published"]["definition"]["capability"] == "review_keywords"

    versions = client.get(f"{BASE}/agent-a/versions", params=ws)
    assert versions.status_code == 200, versions.text
    rows = versions.json()["versions"]
    assert [row["version"] for row in rows] == [2, 1]
    assert {row["version"]: row["status"] for row in rows} == {1: "published", 2: "draft"}
    assert all("definition" not in row for row in rows)


def test_reads_keep_archived_and_preconstraint_history(client, job_db, ws, workspace_id) -> None:
    saved = _save(job_db, workspace_id, "code:x", DEFINITION_V1)
    store = VersionedEntityStore(job_db.dsn_identity, "agent")
    store.publish("code:x", workspace_id, saved.definition_hash)
    assert store.archive_all("code:x", workspace_id) == 1

    detail = client.get(f"{BASE}/code:x", params=ws)
    assert detail.status_code == 200, detail.text
    assert detail.json()["latest"]["status"] == "archived"
    assert detail.json()["published"] is None
    versions = client.get(f"{BASE}/code:x/versions", params=ws)
    assert versions.status_code == 200, versions.text
    assert versions.json()["versions"][0]["status"] == "archived"


def test_catalogs_are_workspace_isolated(client, job_db, ws, workspace_id) -> None:
    saved = _save(job_db, workspace_id, "agent-a", DEFINITION_V1)
    _publish_saved(job_db, workspace_id, "agent-a", saved)
    other = job_db.create_workspace("Other WS")["id"]

    assert client.get(BASE, params={"workspace_id": other}).json()["agents"] == []
    assert client.get(f"{BASE}/agent-a", params={"workspace_id": other}).status_code == 404
    assert client.get(f"{BASE}/agent-a/versions", params={"workspace_id": other}).status_code == 404


def test_workspace_id_required_for_reads(client) -> None:
    assert client.get(BASE).status_code == 422
    assert client.get(f"{BASE}/agent-a").status_code == 422
    assert client.get(f"{BASE}/agent-a/versions").status_code == 422


def test_catalog_is_admin_only_for_non_admin(client, job_db, ws, workspace_id) -> None:
    _save(job_db, workspace_id, "agent-a", DEFINITION_V1)
    other = job_db.create_workspace("Other WS")["id"]
    created = client.post("/api/users", json={"username": "editor-a", "password": "pw1"})
    assert created.status_code == 201, created.text
    editor_id = created.json()["id"]
    bound = client.put(
        f"/api/workspaces/{workspace_id}/members",
        json={"user_id": editor_id, "role": "editor"},
    )
    assert bound.status_code == 200, bound.text

    member = client.__class__(client.app)
    login = member.post("/api/auth/login", json={"username": "editor-a", "password": "pw1"})
    assert login.status_code == 200, login.text
    member.headers["x-agent-legion-request"] = "1"

    assert member.get(BASE, params=ws).status_code == 403
    assert member.get(BASE, params={"workspace_id": other}).status_code == 404


def test_unknown_agent_reads_return_404(client, ws) -> None:
    assert client.get(f"{BASE}/agent-missing", params=ws).status_code == 404
    assert client.get(f"{BASE}/agent-missing/versions", params=ws).status_code == 404


def test_human_writes_are_unmounted_and_write_nothing(client, job_db, ws, workspace_id) -> None:
    saved = _save(job_db, workspace_id, "agent-a", DEFINITION_V1)
    _publish_saved(job_db, workspace_id, "agent-a", saved)
    before = _agent_rows(job_db, workspace_id)
    draft_hash = client.get(f"{BASE}/agent-a", params=ws).json()["latest"]["definition_hash"]
    probes = [
        ("POST", BASE, {"agent_id": "agent-b", **DEFINITION_V1.model_dump(mode="json")}),
        ("PUT", f"{BASE}/agent-a/draft", DEFINITION_V2.model_dump(mode="json")),
        ("POST", f"{BASE}/agent-a/publish", {"expected_hash": draft_hash}),
        ("POST", f"{BASE}/agent-a/rollback", {"version": 1}),
        ("POST", f"{BASE}/agent-a/copy", {"new_agent_id": "agent-b"}),
        ("DELETE", f"{BASE}/agent-a", None),
    ]

    for method, url, payload in probes:
        response = client.request(method, url, params=ws, json=payload)
        assert response.status_code in (404, 405), (method, url, response.text)
        assert _agent_rows(job_db, workspace_id) == before

    paths = client.app.openapi()["paths"]
    assert set(paths[BASE]) == {"get"}
    assert set(paths[f"{BASE}/{{agent_id}}"]) == {"get"}
    assert set(paths[f"{BASE}/{{agent_id}}/versions"]) == {"get"}
    for suffix in ("draft", "publish", "rollback", "copy"):
        assert f"{BASE}/{{agent_id}}/{suffix}" not in paths
    schemas = client.app.openapi()["components"]["schemas"]
    for name in (
        "AgentArchiveResponse",
        "AgentCopyRequest",
        "AgentCreateRequest",
        "AgentDefinitionPayload",
        "AgentPublishRequest",
        "AgentRollbackRequest",
    ):
        assert name not in schemas
    for name in ("AgentDefinitionResponse", "AgentDetailResponse", "AgentVersionResponse"):
        assert name in schemas

    for read in (
        client.get(BASE, params=ws),
        client.get(f"{BASE}/agent-a", params=ws),
        client.get(f"{BASE}/agent-a/versions", params=ws),
    ):
        assert read.status_code == 200
        assert "Deprecation" not in read.headers
