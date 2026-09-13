"""Behavioral tests for the studio-agent catalog read tool endpoints (#633).

``GET /api/studio-agent/tools/workspaces/{id}/agent-definitions`` (latest
versions, all fields), ``POST .../agent-definitions`` (create a NEW Agent
definition draft — capability-derived id, studio-agent attribution),
``.../runtime-models`` (online-worker aggregation) and
``.../agent-runtimes`` (per-runtime tool catalog). Read-only or draft-only
visibility: provider/model declarations stay worker-owned
(EXEC-RUNTIME-MODELS-001) and the tool catalog is a code-defined static
projection (EXEC-RUNTIME-CATALOG-001) — neither is editable through the
tool surface; the create only starts a draft (publishing stays human,
STUDIO-AGENT-001).
"""

from __future__ import annotations

import pytest

from server.app.auth import scoped_tokens
from tests.helpers import seed_workspace_agent_definitions

_WORKFLOW_KEY = "education_video_problems_generation"


def _scoped_client(client, job_db):
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    return scoped, admin_id


@pytest.fixture
def workspace_id(client, job_db) -> str:
    del job_db
    response = client.post("/api/workspaces", json={"id": _WORKFLOW_KEY, "name": "Catalog Reads"})
    assert response.status_code == 200, response.text
    return str(response.json()["workspace"]["id"])


def _draft_agent(scoped, workspace_id: str, agent_id: str, capability: str) -> dict:
    saved = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/agent-definitions/{agent_id}/draft",
        json={"capability": capability, "runtime": "velites", "skill": "g/s"},
    )
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_list_agent_definitions_returns_all_fields_with_version_metadata(
    client, job_db, workspace_id
) -> None:
    scoped, admin_id = _scoped_client(client, job_db)
    _draft_agent(scoped, workspace_id, "agent-a", "review_keywords")
    _draft_agent(scoped, workspace_id, "agent-b", "generate_questions")

    response = scoped.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/agent-definitions")

    assert response.status_code == 200, response.text
    versions = {item["agent_id"]: item for item in response.json()["versions"]}
    assert set(versions) == {"agent-a", "agent-b"}
    item = versions["agent-a"]
    # All definition fields surface (the human list route only carries a
    # summary; the tool surface is the agent's read loop, so it gets the
    # full payload) plus version metadata.
    assert item["version"] == 1
    assert item["status"] == "draft"
    assert item["definition"]["capability"] == "review_keywords"
    assert item["definition"]["runtime"] == "velites"
    assert item["definition"]["skill"] == "g/s"
    assert item["definition"]["tools"] == ["read", "write", "bash"]
    assert item["definition"]["requires_labels"] == {}
    assert item["definition"]["config_schema"] == {}
    assert item["definition_hash"]
    assert item["created_by"] == f"studio-agent:{admin_id}"
    assert item["created_at"]
    assert item["published_at"] is None


def test_list_agent_definitions_latest_beats_published_row(client, job_db, workspace_id) -> None:
    """list_latest semantics: a pending draft v2 shadows the published v1 —
    the agent sees exactly what the next publish would ship. Publishing is a
    HUMAN action (the scoped token gets 403 on the publish endpoint — pinned
    in test_studio_agent_scope.py), so the publish rides the admin client."""
    scoped, _ = _scoped_client(client, job_db)
    _draft_agent(scoped, workspace_id, "agent-a", "review_keywords")
    assert (
        scoped.post(
            f"/api/agent-definitions/agent-a/publish?workspace_id={workspace_id}"
        ).status_code
        == 403
    )
    published = client.post(f"/api/agent-definitions/agent-a/publish?workspace_id={workspace_id}")
    assert published.status_code == 200, published.text
    _draft_agent(scoped, workspace_id, "agent-a", "review_keywords")

    listed = scoped.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/agent-definitions")

    assert listed.status_code == 200
    versions = listed.json()["versions"]
    assert len(versions) == 1
    assert versions[0]["version"] == 2
    assert versions[0]["status"] == "draft"
    assert versions[0]["published_at"] is None


def test_list_agent_definitions_empty_state(client, job_db, workspace_id) -> None:
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/agent-definitions")
    assert response.status_code == 200, response.text
    assert response.json() == {"versions": []}


def test_list_agent_definitions_404_for_unknown_workspace(client, job_db) -> None:
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.get("/api/studio-agent/tools/workspaces/ws-missing/agent-definitions")
    assert response.status_code == 404


def test_list_agent_definitions_sees_seeded_demo_catalog(client, job_db, workspace_id) -> None:
    """The demo seeding helper (tests pin it as the canonical catalog shape)
    surfaces through the tool surface unchanged."""
    scoped, _ = _scoped_client(client, job_db)
    seeded = seed_workspace_agent_definitions(workspace_id)
    assert seeded

    listed = scoped.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/agent-definitions")

    assert listed.status_code == 200
    agent_ids = {item["agent_id"] for item in listed.json()["versions"]}
    assert set(seeded) <= agent_ids


def test_runtime_models_empty_without_online_workers(client, job_db, workspace_id) -> None:
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/runtime-models")
    assert response.status_code == 200, response.text
    assert response.json() == {"runtimes": {}}


def test_runtime_models_404_for_unknown_workspace(client, job_db) -> None:
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.get("/api/studio-agent/tools/workspaces/ws-missing/runtime-models")
    assert response.status_code == 404


def test_runtime_models_aggregates_online_worker_declarations(
    client, job_db, workspace_id, tmp_path
) -> None:
    """The tool surface mirrors the human route's aggregation: only online
    workers of THIS workspace contribute their (runtime, provider, model)
    declarations. Worker registration rides a fresh app (its own registry
    wiring, same pattern as the human route's tests); the scoped read then
    goes through the standard shared-app client."""
    from fastapi.testclient import TestClient

    from server.app.main import create_app
    from tests.helpers.agent_worker_api import authenticate_admin, issue_scoped_token, register

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.workspace_worker_control.resume(workspace_id)
    with TestClient(app) as admin_client:
        authenticate_admin(admin_client)
        credential = issue_scoped_token(admin_client, workspace_id=workspace_id)
        register(
            admin_client,
            credential=credential,
            worker_id="pi-worker",
            runtimes=["pi"],
            models=[{"runtime": "pi", "provider": "deepseek", "model": "v4-flash"}],
            protocol_version=3,
        )
        # 另一 workspace 的 worker 不参与本 workspace 的聚合。
        other_credential = issue_scoped_token(admin_client, workspace_id="other-ws")
        register(
            admin_client,
            credential=other_credential,
            worker_id="other-worker",
            runtimes=["pi"],
            models=[{"runtime": "pi", "provider": "other", "model": "m"}],
            protocol_version=3,
        )

    scoped, _ = _scoped_client(client, job_db)

    response = scoped.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/runtime-models")

    assert response.status_code == 200, response.text
    assert response.json()["runtimes"] == {"pi": {"deepseek": ["v4-flash"]}}


def test_agent_runtimes_lists_per_runtime_tool_catalog(client, job_db, workspace_id) -> None:
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/agent-runtimes")
    assert response.status_code == 200, response.text
    body = response.json()
    # Same shape as the human GET /api/agent-runtimes (#476): per-runtime
    # tool entries with tier + optional activation (forced tier only).
    assert set(body["runtimes"]) == {"pi", "velites"}
    velites = {tool["name"]: tool for tool in body["runtimes"]["velites"]["tools"]}
    assert velites["read"]["tier"] == "default"
    assert velites["uuid"]["tier"] == "opt-in"
    assert velites["validate"]["tier"] == "forced"
    assert velites["validate"]["activation"] == "--require-output"
    assert "activation" not in velites["read"]
    pi_names = [tool["name"] for tool in body["runtimes"]["pi"]["tools"]]
    assert pi_names == ["read", "write", "bash"]


def test_create_agent_definition_starts_draft_with_derived_id_and_attribution(
    client, job_db, workspace_id
) -> None:
    """201 + the draft row exists with agent_id == capability (the tool
    surface takes no explicit id) and the studio-agent:{user_id} stamp."""
    scoped, admin_id = _scoped_client(client, job_db)

    response = scoped.post(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/agent-definitions",
        json={"capability": "review_keywords", "runtime": "velites", "skill": "g/s"},
    )

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["agent_id"] == "review_keywords"
    assert created["version"] == 1
    assert created["status"] == "draft"
    assert created["definition"]["tools"] == ["read", "write", "bash"]
    assert created["created_by"] == f"studio-agent:{admin_id}"
    # The draft row reads back through the list tool (one capability, one
    # latest row — no duplicate entity).
    listed = scoped.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/agent-definitions")
    assert listed.status_code == 200
    assert [item["agent_id"] for item in listed.json()["versions"]] == ["review_keywords"]


def test_create_agent_definition_forwards_optional_fields(client, job_db, workspace_id) -> None:
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.post(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/agent-definitions",
        json={
            "capability": "generate_questions",
            "runtime": "pi",
            "skill": "g/q",
            "tools": ["read"],
            "requires_labels": {"gpu": "a100"},
            "config_schema": {
                "type": "object",
                "properties": {"dry_run": {"type": "boolean"}},
            },
        },
    )
    assert response.status_code == 201, response.text
    definition = response.json()["definition"]
    assert definition["tools"] == ["read"]
    assert definition["requires_labels"] == {"gpu": "a100"}
    assert definition["config_schema"] == {
        "type": "object",
        "properties": {"dry_run": {"type": "boolean"}},
    }


def test_create_agent_definition_conflict_on_occupied_capability(
    client, job_db, workspace_id
) -> None:
    """409 for every occupancy face the create-entry policy guards: an
    existing draft, a published Agent, and a published row hidden behind a
    renamed draft (the #460 P1 face)."""
    scoped, _ = _scoped_client(client, job_db)

    def create(capability: str):
        return scoped.post(
            f"/api/studio-agent/tools/workspaces/{workspace_id}/agent-definitions",
            json={"capability": capability, "runtime": "velites", "skill": "g/s"},
        )

    assert create("review_keywords").status_code == 201
    # Same capability again → 409.
    assert create("review_keywords").status_code == 409
    # Published Agent on the capability → 409 (publishing rides the admin
    # client; the scoped token is refused on the publish endpoint).
    assert create("generate_questions").status_code == 201
    assert (
        scoped.post(
            f"/api/agent-definitions/generate_questions/publish?workspace_id={workspace_id}"
        ).status_code
        == 403
    )
    published = client.post(
        f"/api/agent-definitions/generate_questions/publish?workspace_id={workspace_id}"
    )
    assert published.status_code == 200, published.text
    assert create("generate_questions").status_code == 409
    # Renamed-draft face: the draft renames the capability, but the
    # published v1 still routes — the capability stays occupied.
    renamed = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}"
        "/agent-definitions/generate_questions/draft",
        json={"capability": "generate_questions_v2", "runtime": "velites", "skill": "g/s"},
    )
    assert renamed.status_code == 200, renamed.text
    assert create("generate_questions").status_code == 409
    # An entity keyed by the capability itself also blocks (save_draft on
    # that key would overwrite its draft — never a second entity).
    saved = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/agent-definitions/agent-x/draft",
        json={"capability": "other_capability", "runtime": "velites", "skill": "g/s"},
    )
    assert saved.status_code == 200, saved.text
    assert create("agent-x").status_code == 409


def test_create_agent_definition_rejects_invalid_payload(client, job_db, workspace_id) -> None:
    scoped, _ = _scoped_client(client, job_db)
    base = f"/api/studio-agent/tools/workspaces/{workspace_id}/agent-definitions"

    assert scoped.post(base, json={}).status_code == 422  # missing capability/runtime
    assert (
        scoped.post(base, json={"capability": "c", "runtime": "bogus", "skill": "s"}).status_code
        == 422
    )
    assert (
        scoped.post(
            base,
            json={
                "capability": "c",
                "runtime": "pi",
                "skill": "/abs/path",
            },
        ).status_code
        == 422
    )
    # agent_id is not part of the tool-surface payload — an explicit id is
    # silently DROPPED (pydantic default), so the create still derives the
    # id from the capability: the tool never spawns a second entity.
    explicit = scoped.post(
        base,
        json={"capability": "c", "runtime": "pi", "skill": "s", "agent_id": "explicit"},
    )
    assert explicit.status_code == 201
    assert explicit.json()["agent_id"] == "c"
    # Nothing was created by any of the rejected payloads (only the one
    # deliberate create above).
    listed = scoped.get(base)
    assert [item["agent_id"] for item in listed.json()["versions"]] == ["c"]


def test_create_agent_definition_404_for_unknown_workspace(client, job_db) -> None:
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.post(
        "/api/studio-agent/tools/workspaces/ws-missing/agent-definitions",
        json={"capability": "c", "runtime": "pi", "skill": "s"},
    )
    assert response.status_code == 404


def test_workspace_bound_token_is_refused_on_other_workspaces(client, job_db) -> None:
    """Schema v45 binding, asserted once behaviorally for the new reads: a
    run token bound to workspace A gets 403 on workspace B's catalog reads
    — and on the create write too."""
    workspace_id = str(
        job_db.create_workspace("Catalog Reads", default_workflow_key=_WORKFLOW_KEY)["id"]
    )
    other_id = str(
        job_db.create_workspace("Catalog Reads B", default_workflow_key=_WORKFLOW_KEY)["id"]
    )
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    bound = client.__class__(client.app)
    bound.headers["authorization"] = (
        f"Bearer {scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id=workspace_id)}"
    )
    base = "/api/studio-agent/tools/workspaces"
    create_payload = {"capability": "c", "runtime": "pi", "skill": "s"}
    assert bound.post(
        f"{base}/{workspace_id}/agent-definitions", json=create_payload
    ).status_code in (200, 201)
    assert (
        bound.post(f"{base}/{other_id}/agent-definitions", json=create_payload).status_code == 403
    )
    for tail in ("agent-definitions", "runtime-models", "agent-runtimes"):
        assert bound.get(f"{base}/{workspace_id}/{tail}").status_code == 200, tail
        assert bound.get(f"{base}/{other_id}/{tail}").status_code == 403, tail
