"""GET/PUT /api/workspaces/{id}/workflow-draft (Studio YAML draft store)."""

from __future__ import annotations

_DRAFT_YAML = "key: wf\nlabel: Draft\nnodes:\n  intake:\n    capability: intake\n"


def _url(workspace_id: str) -> str:
    return f"/api/workspaces/{workspace_id}/workflow-draft"


def test_get_returns_structured_empty_state(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-empty", default_workflow_key="wf")

    response = client.get(_url(workspace["id"]))

    assert response.status_code == 200
    assert response.json() == {"definition_yaml": None, "updated_at": None}


def test_put_then_get_roundtrip(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store", default_workflow_key="wf")

    put = client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})

    assert put.status_code == 200
    assert put.json()["definition_yaml"] == _DRAFT_YAML
    assert put.json()["updated_at"]
    got = client.get(_url(workspace["id"]))
    assert got.json()["definition_yaml"] == _DRAFT_YAML
    assert got.json()["updated_at"] == put.json()["updated_at"]


def test_put_overwrites_the_previous_draft(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-overwrite", default_workflow_key="wf")
    client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})

    updated = client.put(_url(workspace["id"]), json={"definition_yaml": "key: wf\nlabel: V2\n"})

    assert updated.status_code == 200
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] == "key: wf\nlabel: V2\n"


def test_put_rejects_blank_draft(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-blank", default_workflow_key="wf")

    for blank in ("", "   \n  "):
        response = client.put(_url(workspace["id"]), json={"definition_yaml": blank})
        assert response.status_code == 422
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] is None


def test_unknown_workspace_gets_404(client) -> None:
    assert client.get(_url("no-such-ws")).status_code == 404
    put = client.put(_url("no-such-ws"), json={"definition_yaml": _DRAFT_YAML})
    assert put.status_code == 404


def test_drafts_are_isolated_between_workspaces(client, job_db) -> None:
    first = job_db.create_workspace("ws-store-a", default_workflow_key="wf")
    second = job_db.create_workspace("ws-store-b", default_workflow_key="wf")

    client.put(_url(first["id"]), json={"definition_yaml": _DRAFT_YAML})

    assert client.get(_url(second["id"])).json() == {
        "definition_yaml": None,
        "updated_at": None,
    }


def test_anonymous_gets_401(anon_client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-anon", default_workflow_key="wf")

    assert anon_client.get(_url(workspace["id"])).status_code == 401
    put = anon_client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})
    assert put.status_code == 401


def test_cookie_put_without_csrf_header_gets_403(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-csrf", default_workflow_key="wf")
    bare = client.__class__(client.app)
    session = client.cookies.get("agent_legion_session")
    assert session
    bare.cookies.set("agent_legion_session", session)

    response = bare.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})

    assert response.status_code == 403
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] is None


def test_scoped_token_cannot_write_but_can_read(client, job_db) -> None:
    """STUDIO-AGENT-001: PUT mounts reject_studio_agent_scope (the mechanical
    inventory in test_studio_agent_scope.py pins this); the GET stays readable
    like the other studio_secured reads."""
    from server.app.auth import scoped_tokens

    workspace = job_db.create_workspace("ws-store-scoped", default_workflow_key="wf")
    client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {scoped_tokens.mint_scoped_token(job_db, admin_id)}"

    put = scoped.put(_url(workspace["id"]), json={"definition_yaml": "key: evil\n"})
    assert put.status_code == 403
    assert "Studio agent scope" in put.json()["detail"]
    got = scoped.get(_url(workspace["id"]))
    assert got.status_code == 200
    assert got.json()["definition_yaml"] == _DRAFT_YAML


# --- #633 codex review P1-1: the human PUT's expected_updated_at is a real
# CAS base (stale → 409 with the current draft), and an absent field keeps
# the legacy last-write-wins semantics for old clients.


def test_put_with_stale_expected_updated_at_gets_409_with_current_draft(client, job_db) -> None:
    """The exact failure CAS was built to prevent: an agent-saved draft must
    NOT be silently overwritten by a human autosave carrying a stale base —
    the 409 detail carries the current draft (same payload shape as the tool
    surface) so the editor can rebase in one round-trip."""
    workspace = job_db.create_workspace("ws-store-cas-stale", default_workflow_key="wf")
    first = client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})
    assert first.status_code == 200
    stale_base = first.json()["updated_at"]

    # The agent tool surface (or another session) saves a newer draft.
    agent_yaml = "key: wf\nlabel: Agent v2\n"
    agent_saved = client.put(
        f"/api/studio-agent/tools/workspaces/{workspace['id']}/workflow/draft",
        json={"definition_yaml": agent_yaml, "expected_updated_at": stale_base},
        headers=_admin_bearer(client, job_db),
    )
    assert agent_saved.status_code == 200, agent_saved.text

    # The human editor's PUT with the now-stale base loses the race loudly.
    conflict = client.put(
        _url(workspace["id"]),
        json={
            "definition_yaml": "key: wf\nlabel: Human edit\n",
            "expected_updated_at": stale_base,
        },
    )

    assert conflict.status_code == 409, conflict.text
    detail = conflict.json()["detail"]
    assert detail["expected_updated_at"] == stale_base
    assert detail["current_draft"]["definition_yaml"] == agent_yaml
    assert detail["current_draft"]["updated_at"]
    # The agent-saved draft was NOT overwritten.
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] == agent_yaml

    # Rebasing with the current timestamp succeeds.
    rebased = client.put(
        _url(workspace["id"]),
        json={
            "definition_yaml": "key: wf\nlabel: Human edit\n",
            "expected_updated_at": detail["current_draft"]["updated_at"],
        },
    )
    assert rebased.status_code == 200, rebased.text
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] == (
        "key: wf\nlabel: Human edit\n"
    )


def test_put_without_expected_updated_at_keeps_last_write_wins(client, job_db) -> None:
    """Backward compatibility: an absent/null expected_updated_at keeps the
    documented two-tab last-write-wins semantics (no 409 for legacy callers)."""
    workspace = job_db.create_workspace("ws-store-cas-none", default_workflow_key="wf")
    first = client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})
    base = first.json()["updated_at"]

    # A newer draft lands (any writer), then a legacy PUT without the field.
    newer = client.put(
        _url(workspace["id"]),
        json={
            "definition_yaml": "key: wf\nlabel: Newer\n",
            "expected_updated_at": base,
        },
    )
    assert newer.status_code == 200
    legacy = client.put(_url(workspace["id"]), json={"definition_yaml": "key: wf\nlabel: Legacy\n"})

    assert legacy.status_code == 200
    assert legacy.json()["definition_yaml"] == "key: wf\nlabel: Legacy\n"
    # The same for an explicit null.
    explicit_null = client.put(
        _url(workspace["id"]),
        json={
            "definition_yaml": "key: wf\nlabel: Explicit null\n",
            "expected_updated_at": None,
        },
    )
    assert explicit_null.status_code == 200
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] == (
        "key: wf\nlabel: Explicit null\n"
    )


def test_put_with_matching_expected_updated_at_succeeds(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-cas-ok", default_workflow_key="wf")
    first = client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})
    base = first.json()["updated_at"]

    matched = client.put(
        _url(workspace["id"]),
        json={
            "definition_yaml": "key: wf\nlabel: V2\n",
            "expected_updated_at": base,
        },
    )

    assert matched.status_code == 200, matched.text
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] == "key: wf\nlabel: V2\n"


def test_put_with_never_saved_conflicts_when_a_draft_already_exists(client, job_db) -> None:
    """never-saved is only valid while the draft truly does not exist; a
    caller that last saw a draft must not insert over its absence."""
    workspace = job_db.create_workspace("ws-store-cas-ns", default_workflow_key="wf")
    client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})

    conflict = client.put(
        _url(workspace["id"]),
        json={"definition_yaml": "key: wf\nlabel: V2\n", "expected_updated_at": "never-saved"},
    )

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["current_draft"]["definition_yaml"] == _DRAFT_YAML


# --- #633 codex review P2-2: an unparseable CAS token is a 422 (input
# error), never a DB error (500) from the timestamptz cast.


def test_put_with_invalid_expected_updated_at_gets_422(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-cas-bad", default_workflow_key="wf")

    for bad in ("garbage", "2026-13-45T99:99:99+00:00", "yesterday"):
        response = client.put(
            _url(workspace["id"]),
            json={"definition_yaml": _DRAFT_YAML, "expected_updated_at": bad},
        )
        assert response.status_code == 422, bad
        assert "expected_updated_at must be an ISO timestamp" in response.text
    # Nothing was stored.
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] is None


def _admin_bearer(client, job_db) -> dict[str, str]:
    """Scoped-token Authorization header for the studio-agent tool surface."""
    from server.app.auth import scoped_tokens

    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id)
    return {"authorization": f"Bearer {token}"}
