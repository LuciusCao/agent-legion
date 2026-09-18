"""Cross-workspace access matrix for job-id-shaped routes (#710).

``require_workspace_access`` historically only checked membership when the
route carried a ``workspace_id``; the 12 bare ``/jobs/{job_id}`` endpoints
(read detail/artifacts/logs/token-usage, delete, rerun/run-to/continue,
upgrade-workflow, and the invalid-subpath catch-all) therefore let any
logged-in user read, mutate, or delete another workspace's jobs. These
tests pin the fixed behavior: the job's own workspace is the authorization
scope, unknown jobs 404 (enumeration-safe), and a workspace prefix that
does not match the job's actual workspace is rejected even for members.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import publish_legacy_intake_revision, seed_workspace_agent_definitions

CSRF = {"x-agent-legion-request": "1"}


def _create_member(client, username: str, password: str) -> str:
    response = client.post(
        "/api/users",
        json={"username": username, "password": password},
        headers=CSRF,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _member_client(client, username: str, password: str):
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member


def _make_job(client, workspace_id: str) -> str:
    seed_workspace_agent_definitions(workspace_id)
    publish_legacy_intake_revision(client.app.state.job_db, workspace_id)
    created = client.post(
        f"/api/workspaces/{workspace_id}/job-batches",
        json={
            "workflow_key": workspace_id,
            "source_kind": "direct_ids",
            "knowledge_point_ids": ["Q001"],
        },
        headers=CSRF,
    )
    assert created.status_code == 200, created.text
    return created.json()["jobs"][0]["id"]


@pytest.fixture
def two_workspaces_with_jobs(client) -> tuple[str, str, str, str]:
    """(workspace A id, workspace B id, job in A, job in B)."""
    ws_a = client.post(
        "/api/workspaces", json={"id": "ws_alpha", "name": "Alpha"}, headers=CSRF
    ).json()["workspace"]["id"]
    ws_b = client.post(
        "/api/workspaces", json={"id": "ws_beta", "name": "Beta"}, headers=CSRF
    ).json()["workspace"]["id"]
    return ws_a, ws_b, _make_job(client, ws_a), _make_job(client, ws_b)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/jobs/{job_id}"),
        ("GET", "/api/jobs/{job_id}/artifacts/some-artifact"),
        ("GET", "/api/jobs/{job_id}/artifacts/some-artifact/raw"),
        ("GET", "/api/jobs/{job_id}/runs/1/log"),
        ("GET", "/api/jobs/{job_id}/token-usage"),
        ("GET", "/api/jobs/{job_id}/runs/1/token-usage"),
        ("DELETE", "/api/jobs/{job_id}"),
        ("POST", "/api/jobs/{job_id}/nodes/node-1/rerun"),
        ("POST", "/api/jobs/{job_id}/run-to"),
        ("POST", "/api/jobs/{job_id}/continue"),
        ("POST", "/api/jobs/{job_id}/upgrade-workflow"),
        ("GET", "/api/jobs/{job_id}/invalid/subpath"),
    ],
)
def test_non_member_cannot_touch_other_workspace_job(
    client, two_workspaces_with_jobs, method, path
) -> None:
    """A logged-in non-member of workspace A gets 404 on every job-id route
    pointing at A's job — regardless of their role anywhere else."""
    ws_a, ws_b, job_a, _job_b = two_workspaces_with_jobs
    _create_member(client, "outsider", "pw-outsider")
    outsider = _member_client(client, "outsider", "pw-outsider")
    # Sanity: the outsider IS a functioning account (its own view works).
    assert outsider.get("/api/auth/me").status_code == 200

    request = getattr(outsider, method.lower())
    response = request(path.format(job_id=job_a))
    assert response.status_code == 404, (method, path, response.text)
    # The job must survive the DELETE attempt.
    if method == "DELETE":
        assert client.get(f"/api/jobs/{job_a}", headers=CSRF).status_code == 200
    _ = ws_b


def test_other_workspace_viewer_cannot_read_or_delete(client, two_workspaces_with_jobs) -> None:
    """Membership in workspace B grants nothing on workspace A's jobs —
    the IDOR's exact shape from the review (#710)."""
    ws_a, ws_b, job_a, _job_b = two_workspaces_with_jobs
    member_id = _create_member(client, "beta-viewer", "pw-beta")
    job_db = client.app.state.job_db
    job_db.upsert_workspace_member(ws_b, member_id, "viewer")

    beta = _member_client(client, "beta-viewer", "pw-beta")
    assert beta.get(f"/api/jobs/{job_a}").status_code == 404
    assert beta.get(f"/api/jobs/{job_a}/token-usage").status_code == 404
    assert beta.delete(f"/api/jobs/{job_a}").status_code == 404
    assert beta.post(f"/api/jobs/{job_a}/run-to").status_code == 404
    assert client.get(f"/api/jobs/{job_a}", headers=CSRF).status_code == 200


def test_member_roles_still_work_on_own_workspace(client, two_workspaces_with_jobs) -> None:
    """No regression: the guard change must not lock members out of their
    own workspace's job routes."""
    ws_a, _ws_b, job_a, _job_b = two_workspaces_with_jobs
    member_id = _create_member(client, "alpha-viewer", "pw-alpha")
    client.app.state.job_db.upsert_workspace_member(ws_a, member_id, "viewer")

    viewer = _member_client(client, "alpha-viewer", "pw-alpha")
    assert viewer.get(f"/api/jobs/{job_a}").status_code == 200
    assert viewer.get(f"/api/jobs/{job_a}/token-usage").status_code == 200
    # Viewer of the owning workspace still cannot mutate (403, not 404:
    # membership is visible, the role is not).
    assert viewer.delete(f"/api/jobs/{job_a}").status_code == 403
    assert viewer.post(f"/api/jobs/{job_a}/run-to").status_code == 403


def test_workspace_prefix_cannot_borrow_foreign_job_id(client, two_workspaces_with_jobs) -> None:
    """``/workspaces/{own}/jobs/{foreign_job}`` mixed shapes 404 even for
    the workspace's own editor — the path scope and the job's real
    workspace must agree."""
    ws_a, ws_b, job_a, _job_b = two_workspaces_with_jobs
    member_id = _create_member(client, "beta-editor", "pw-beta-e")
    client.app.state.job_db.upsert_workspace_member(ws_b, member_id, "editor")

    beta = _member_client(client, "beta-editor", "pw-beta-e")
    # Beta's own workspace is fully accessible...
    assert beta.get(f"/api/workspaces/{ws_b}").status_code == 200
    # ...but cannot borrow Alpha's job under its own prefix.
    assert beta.get(f"/api/workspaces/{ws_b}/jobs/{job_a}/approvals").status_code == 404
    assert (
        beta.post(
            f"/api/workspaces/{ws_b}/jobs/{job_a}/nodes/node-1/approval",
            json={"decision": "approved"},
        ).status_code
        == 404
    )


def test_admin_still_passes_and_unknown_job_404s(client, two_workspaces_with_jobs) -> None:
    ws_a, _ws_b, job_a, _job_b = two_workspaces_with_jobs
    # Admin keeps cross-workspace access by design.
    assert client.get(f"/api/jobs/{job_a}", headers=CSRF).status_code == 200
    # Unknown job ids 404 for logged-in non-admins (enumeration-safe)...
    _create_member(client, "enum-probe", "pw-enum")
    probe = _member_client(client, "enum-probe", "pw-enum")
    assert probe.get("/api/jobs/does_not_exist_job").status_code == 404
    assert probe.get("/api/jobs/does_not_exist_job/token-usage").status_code == 404


def test_404_detail_does_not_leak_job_existence(client, two_workspaces_with_jobs) -> None:
    """The bare-route guard's 404 detail must be identical for a missing job
    and a foreign-workspace job — otherwise the status-code-uniform 404s
    still form a per-request existence oracle (review R1 P3)."""
    ws_a, _ws_b, job_a, _job_b = two_workspaces_with_jobs
    _create_member(client, "detail-probe", "pw-detail")
    probe = _member_client(client, "detail-probe", "pw-detail")
    missing = probe.get("/api/jobs/no_such_job_anywhere").json()["detail"]
    foreign = probe.get(f"/api/jobs/{job_a}").json()["detail"]
    assert missing == foreign == "Job not found"


def test_workspace_bound_scoped_token_cannot_read_other_workspace_job(
    client, two_workspaces_with_jobs
) -> None:
    """A run token bound to workspace B must not read workspace A's jobs even
    when the initiating user is a member of both — the #158 binding the bare
    job routes previously bypassed (review R1 P3)."""
    from server.app.auth import scoped_tokens

    ws_a, ws_b, job_a, job_b = two_workspaces_with_jobs
    member_id = _create_member(client, "dual-member", "pw-dual")
    job_db = client.app.state.job_db
    job_db.upsert_workspace_member(ws_a, member_id, "viewer")
    job_db.upsert_workspace_member(ws_b, member_id, "viewer")

    token = scoped_tokens.mint_scoped_token(job_db, member_id, workspace_id=ws_b)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"

    # Bound workspace's job stays readable...
    assert scoped.get(f"/api/jobs/{job_b}").status_code == 200
    # ...the other workspace's job is refused even though the minter is a
    # member there (404, same shape as any foreign job). The binding guard
    # covers the bare job routes; the generic workspace-prefixed surface
    # keeps its original semantics (its own scoped contracts live on the
    # studio-agent tool / chat-read surfaces).
    assert scoped.get(f"/api/jobs/{job_a}").status_code == 404
    assert scoped.get(f"/api/jobs/{job_a}/token-usage").status_code == 404


def test_admin_minted_scoped_token_stays_workspace_bound(client, two_workspaces_with_jobs) -> None:
    """Scoped tokens inherit the minter's role, so an admin-minted run token
    must NOT take the admin fast path past the workspace binding (review
    R2 P2) — the leaked-token blast radius stays the bound workspace."""
    from server.app.auth import scoped_tokens

    ws_a, ws_b, job_a, _job_b = two_workspaces_with_jobs
    job_db = client.app.state.job_db
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id=ws_b)

    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    assert scoped.get(f"/api/jobs/{job_a}").status_code == 404
    assert scoped.get(f"/api/jobs/{job_a}/token-usage").status_code == 404
    # The admin session itself still passes everywhere (fast path intact).
    assert client.get(f"/api/jobs/{job_a}", headers=CSRF).status_code == 200


def test_batch_endpoints_do_not_leak_foreign_job_existence(
    client, two_workspaces_with_jobs
) -> None:
    """Batch mutation results must answer 'not_found' identically for a
    foreign-workspace job and a truly missing id — the per-item
    wrong_workspace/not_found split was a deterministic existence oracle
    (review R1 P2)."""
    ws_a, ws_b, job_a, _job_b = two_workspaces_with_jobs
    member_id = _create_member(client, "batch-probe", "pw-batch")
    client.app.state.job_db.upsert_workspace_member(ws_b, member_id, "editor")

    beta = _member_client(client, "batch-probe", "pw-batch")
    response = beta.request(
        "DELETE",
        f"/api/workspaces/{ws_b}/jobs/batch",
        json={"job_ids": [job_a, "totally_unknown_job"]},
    )
    assert response.status_code == 200, response.text
    results = {item["job_id"]: item for item in response.json()["results"]}
    assert results[job_a]["reason_code"] == "not_found"
    assert results["totally_unknown_job"]["reason_code"] == "not_found"
    assert results[job_a]["message"] == results["totally_unknown_job"]["message"]
    # And the foreign job survives.
    assert client.get(f"/api/jobs/{job_a}", headers=CSRF).status_code == 200
    _ = ws_a


# Every batch mutation surface that takes explicit job_ids must answer a
# foreign-workspace id and an unknown id identically ((reason_code, message)
# pairs) — a per-endpoint literal drifting apart would silently rebuild the
# existence oracle (review R2 P3).
_BATCH_SURFACES: list[tuple[str, str, dict]] = [
    ("DELETE", "/api/workspaces/{ws}/jobs/batch", {"job_ids": ["{job_a}", "{unknown}"]}),
    (
        "POST",
        "/api/workspaces/{ws}/jobs/batch-rerun",
        {"job_ids": ["{job_a}", "{unknown}"], "from_failed_node": True},
    ),
    (
        "POST",
        "/api/workspaces/{ws}/jobs/batch-run-to",
        {"job_ids": ["{job_a}", "{unknown}"], "target_node_key": "any"},
    ),
    (
        "POST",
        "/api/workspaces/{ws}/jobs/batch-upgrade-workflow",
        {"job_ids": ["{job_a}", "{unknown}"]},
    ),
]


@pytest.mark.parametrize(("method", "path", "payload"), _BATCH_SURFACES)
def test_all_batch_surfaces_answer_foreign_and_unknown_identically(
    client, two_workspaces_with_jobs, method, path, payload
) -> None:
    ws_a, ws_b, job_a, _job_b = two_workspaces_with_jobs
    member_id = _create_member(client, "batch-matrix", "pw-bm")
    client.app.state.job_db.upsert_workspace_member(ws_b, member_id, "editor")

    beta = _member_client(client, "batch-matrix", "pw-bm")
    url = path.format(ws=ws_b)
    body = {
        key: [value.replace("{job_a}", job_a).replace("{unknown}", "no_such_job") for value in val]
        if isinstance(val, list)
        else val
        for key, val in payload.items()
    }
    response = beta.request(method, url, json=body)
    assert response.status_code == 200, (method, url, response.text)
    items = response.json().get("results") or response.json().get("jobs") or []
    by_id = {item.get("job_id"): item for item in items}
    foreign = by_id.get(job_a)
    unknown = by_id.get("no_such_job")
    assert foreign is not None and unknown is not None, (method, url, items)
    assert foreign.get("reason_code") == unknown.get("reason_code"), (method, url)
    assert foreign.get("message") == unknown.get("message"), (method, url)
    assert client.get(f"/api/jobs/{job_a}", headers=CSRF).status_code == 200
    _ = ws_a


# --- Skill catalog reads (red-team V1 on #710's audit) -----------------------
#
# GET /api/agent-catalog/skills/{workspace}/{capability} carried no
# workspace_id path parameter, so the generic secured() guard passed any
# logged-in user through: a low-privilege account could read any
# workspace's full skill content (prompts, contracts, scripts). The fix
# membership-checks the workspace segment of the skill key.


def _make_skill_repo(repo: Path) -> None:
    import subprocess

    def git(*args: str) -> None:
        env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)

    repo.mkdir(parents=True)
    (repo / "SKILL.md").write_text("# secret skill\n", encoding="utf-8")
    git("init", "-q")
    git("add", ".")
    git("commit", "-q", "-m", "init", "--no-gpg-sign")


def test_skill_catalog_requires_workspace_membership(client, tmp_path, monkeypatch) -> None:
    base = tmp_path / "home" / ".agents" / "skills"
    skill_key = "ws_victim/secret_capability"
    _make_skill_repo(base / skill_key)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    # Any logged-in non-member (no workspace at all) is refused with the
    # unknown-skill 404 shape.
    _create_member(client, "skill-outsider", "pw-skill")
    outsider = _member_client(client, "skill-outsider", "pw-skill")

    def _read(c, ws="ws_victim"):
        return c.get(f"/api/agent-catalog/skills/{skill_key}", params={"workspace_id": ws})

    # Non-member probe before the workspace exists: uniform workspace-shaped
    # 404 from the router-level guard — the same refusal every other
    # query-scoped route gives, so no oracle between "no such workspace" and
    # "not a member" on this surface.
    response = _read(outsider)
    assert response.status_code == 404
    assert response.json()["detail"] == "Workspace not found"
    missing_ws = outsider.get(
        "/api/agent-catalog/skills/no_such_ws/secret_capability",
        params={"workspace_id": "no_such_ws"},
    )
    assert missing_ws.status_code == 404
    assert missing_ws.json()["detail"] == "Workspace not found"

    # A member of the owning workspace reads it fine.
    ws_victim = client.post(
        "/api/workspaces", json={"id": "ws_victim", "name": "Victim"}, headers=CSRF
    ).json()["workspace"]["id"]
    member_id = client.app.state.job_db.get_user_credentials("skill-outsider")["id"]
    client.app.state.job_db.upsert_workspace_member(ws_victim, member_id, "viewer")
    ok = _read(outsider, ws_victim)
    assert ok.status_code == 200
    assert any(f["path"] == "SKILL.md" for f in ok.json()["files"])

    # A member of ANOTHER workspace cannot read it through their own scope:
    # the key's first segment is an existing workspace's id (create_skill
    # layout), so workspace-directory strictness pins it to ws_victim.
    ws_other = client.post(
        "/api/workspaces", json={"id": "ws_other", "name": "Other"}, headers=CSRF
    ).json()["workspace"]["id"]
    other_member = _create_member(client, "skill-other", "pw-other")
    client.app.state.job_db.upsert_workspace_member(ws_other, other_member, "viewer")
    other = _member_client(client, "skill-other", "pw-other")
    cross = other.get(f"/api/agent-catalog/skills/{skill_key}", params={"workspace_id": ws_other})
    assert cross.status_code == 404
    assert cross.json()["detail"] == "Skill not found"

    # Admin passes regardless of membership.
    assert _read(client, ws_victim).status_code == 200


def test_skill_catalog_scoped_binding_is_enforced(client, tmp_path, monkeypatch) -> None:
    """codex P1 on #745: a workspace-bound run token must not read another
    workspace's skills through the catalog/validate/tags surfaces — even
    when the minter is a member (or admin) of both."""
    from server.app.auth import scoped_tokens

    base = tmp_path / "home" / ".agents" / "skills"
    skill_key = "ws_victim/secret_capability"
    _make_skill_repo(base / skill_key)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ws_a = client.post(
        "/api/workspaces", json={"id": "ws_victim", "name": "Victim"}, headers=CSRF
    ).json()["workspace"]["id"]
    ws_b = client.post(
        "/api/workspaces", json={"id": "ws_reader", "name": "Reader"}, headers=CSRF
    ).json()["workspace"]["id"]
    member_id = _create_member(client, "skill-dual", "pw-dual")
    job_db = client.app.state.job_db
    job_db.upsert_workspace_member(ws_a, member_id, "viewer")
    job_db.upsert_workspace_member(ws_b, member_id, "viewer")

    token = scoped_tokens.mint_scoped_token(job_db, member_id, workspace_id=ws_b)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    # Bound to ws_reader: reading ws_victim's workspace-directory skill via
    # ws_victim's scope is refused (binding, before any role logic) — even
    # though the minter is a member of ws_victim.
    response = scoped.get(f"/api/agent-catalog/skills/{skill_key}", params={"workspace_id": ws_a})
    assert response.status_code == 404
    assert response.json()["detail"] == "Workspace not found"
    # The tags surface shares the binding guard.
    skill_path = str(base / skill_key)
    assert (
        scoped.get(
            "/api/skills/tags", params={"path": skill_path, "workspace_id": ws_a}
        ).status_code
        == 404
    )
    # Sanity through the minter's own workspace: the ws_victim-directory key
    # is refused by the directory strictness (key belongs to ws_victim), and
    # a group-directory key under the same scope reads fine.
    group_key = "shared-group/public-skill"
    _make_skill_repo(base / group_key)
    assert (
        scoped.get(
            f"/api/agent-catalog/skills/{skill_key}", params={"workspace_id": ws_b}
        ).status_code
        == 404
    )
    assert (
        scoped.get(
            f"/api/agent-catalog/skills/{group_key}", params={"workspace_id": ws_b}
        ).status_code
        == 200
    )


def test_skill_catalog_group_directory_is_shared_across_workspaces(
    client, tmp_path, monkeypatch
) -> None:
    """codex P2 on #745: the demo layout has a GROUP directory whose name
    differs from every workspace id (hyphens vs underscores). Such keys are
    shared read surfaces: any member can read them through their own
    workspace scope — the previous key-equals-workspace assumption locked
    the demo out."""
    base = tmp_path / "home" / ".agents" / "skills"
    group_key = "education-video-problems-generation/write-script"
    _make_skill_repo(base / group_key)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ws_demo = client.post(
        "/api/workspaces",
        json={"id": "education_video_problems_generation", "name": "Demo"},
        headers=CSRF,
    ).json()["workspace"]["id"]
    member_id = _create_member(client, "demo-member", "pw-demo")
    client.app.state.job_db.upsert_workspace_member(ws_demo, member_id, "viewer")
    member = _member_client(client, "demo-member", "pw-demo")
    ok = member.get(f"/api/agent-catalog/skills/{group_key}", params={"workspace_id": ws_demo})
    assert ok.status_code == 200, ok.text
    assert any(f["path"] == "SKILL.md" for f in ok.json()["files"])


def test_skill_catalog_case_variant_key_is_refused(client, tmp_path, monkeypatch) -> None:
    """red-team R8 P1-1: on case-insensitive filesystems (macOS) a mixed-case
    first segment misses the exact workspace lookup, was treated as a group
    directory, and the FS resolved it to the real repo — cross-workspace
    private-skill read. Case variants are now refused outright."""
    base = tmp_path / "home" / ".agents" / "skills"
    skill_key = "ws_victim/private_cap"
    _make_skill_repo(base / skill_key)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ws_attacker = client.post(
        "/api/workspaces", json={"id": "ws_attacker", "name": "Attacker"}, headers=CSRF
    ).json()["workspace"]["id"]
    ws_victim = client.post(
        "/api/workspaces", json={"id": "ws_victim", "name": "Victim"}, headers=CSRF
    ).json()["workspace"]["id"]
    member_id = _create_member(client, "case-probe", "pw-case")
    client.app.state.job_db.upsert_workspace_member(ws_attacker, member_id, "editor")
    attacker = _member_client(client, "case-probe", "pw-case")

    for variant in ("WS_Victim/private_cap", "Ws_Victim/private_cap", "WS_VICTIM/private_cap"):
        response = attacker.get(
            f"/api/agent-catalog/skills/{variant}", params={"workspace_id": ws_attacker}
        )
        assert response.status_code == 404, variant
    # The exact lowercase key through the victim scope still works for
    # members (sanity that the resolver did not lock everything out).
    client.app.state.job_db.upsert_workspace_member(ws_victim, member_id, "viewer")
    assert (
        attacker.get(
            f"/api/agent-catalog/skills/{skill_key}", params={"workspace_id": ws_victim}
        ).status_code
        == 200
    )


def test_skill_catalog_empty_workspace_id_is_rejected(client, tmp_path, monkeypatch) -> None:
    """red-team R8 P2-1: an empty workspace_id query used to skip the
    router-level membership check entirely (guard treats falsy as absent) —
    any logged-in user read group skills with zero workspace relation. The
    parameter is now min_length=1 (422), fail-closed."""
    base = tmp_path / "home" / ".agents" / "skills"
    _make_skill_repo(base / "shared-group/group_cap")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _create_member(client, "empty-ws-probe", "pw-empty")
    probe = _member_client(client, "empty-ws-probe", "pw-empty")
    response = probe.get("/api/agent-catalog/skills/shared-group/group_cap?workspace_id=")
    assert response.status_code == 422
    assert (
        probe.get(
            "/api/skills/tags",
            params={"path": str(base / "shared-group/group_cap"), "workspace_id": ""},
        ).status_code
        == 422
    )


def test_group_skill_write_is_refused_for_scoped_tokens(
    client, job_db, tmp_path, monkeypatch
) -> None:
    """red-team R8 P1-2: group directories are shared runtime surfaces —
    every referencing workspace executes their prompts/validators — so
    committing/tagging into them must not be reachable by any workspace's
    scoped token (the previous model allowed exactly that: 201 from a
    foreign workspace's run token poisoning shared skills)."""
    from server.app.auth import scoped_tokens

    base = tmp_path / "home" / ".agents" / "skills"
    group_key = "shared-group/group_cap"
    _make_skill_repo(base / group_key)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ws_a = client.post("/api/workspaces", json={"id": "ws_a", "name": "A"}, headers=CSRF).json()[
        "workspace"
    ]["id"]

    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id=ws_a)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"

    # Reads of the group skill stay fine through any workspace scope...
    assert (
        scoped.get(f"/api/studio-agent/tools/workspaces/{ws_a}/skills/{group_key}").status_code
        == 200
    )
    # ...but the write surface refuses group keys outright.
    import subprocess as sp

    repo = base / group_key
    head_before = sp.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    write = scoped.post(
        f"/api/studio-agent/tools/workspaces/{ws_a}/skills/{group_key}/versions",
        json={
            "files": [{"path": "SKILL.md", "content": "# poisoned\n"}],
            "new_tag": "v9.9.9",
            "message": "poison",
        },
    )
    assert write.status_code == 404, write.text
    head_after = sp.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head_before == head_after  # repo untouched


def test_scoped_token_cannot_reach_unguarded_job_group_posts(client, job_db) -> None:
    """red-team R8 P2-2: the job guard's scoped effecting short-circuit
    assumed every POST under job_group refuses scoped tokens; the two routes
    without reject_studio_agent_scope (batch-rerun/preview, stress events)
    used to skip the membership check for scoped callers. Both now mount the
    refusal explicitly."""
    from server.app.auth import scoped_tokens

    ws_victim = client.post(
        "/api/workspaces", json={"id": "ws_victim", "name": "Victim"}, headers=CSRF
    ).json()["workspace"]["id"]
    ws_attacker = client.post(
        "/api/workspaces", json={"id": "ws_attacker", "name": "Attacker"}, headers=CSRF
    ).json()["workspace"]["id"]
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id=ws_attacker)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"

    preview = scoped.post(
        f"/api/workspaces/{ws_victim}/jobs/batch-rerun/preview",
        json={"node_key": "n1"},
    )
    assert preview.status_code == 403, preview.text
    assert "cannot take effect" in preview.json()["detail"]


def test_skill_catalog_no_workspace_existence_oracle(client, tmp_path, monkeypatch) -> None:
    """red-team R9 P3-1 on #745: a member probing candidate first segments
    through the catalog must not distinguish "existing workspace id" (404)
    from "no such workspace" (200-with-available=false) — a missing group
    directory now 404s identically."""
    base = tmp_path / "home" / ".agents" / "skills"
    _make_skill_repo(base / "ws_victim/private_cap")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    client.post("/api/workspaces", json={"id": "ws_victim", "name": "Victim"}, headers=CSRF)
    ws_probe = client.post(
        "/api/workspaces", json={"id": "ws_probe", "name": "Probe"}, headers=CSRF
    ).json()["workspace"]["id"]
    member_id = _create_member(client, "oracle-probe", "pw-oracle")
    client.app.state.job_db.upsert_workspace_member(ws_probe, member_id, "viewer")
    probe = _member_client(client, "oracle-probe", "pw-oracle")

    existing_ws = probe.get(
        "/api/agent-catalog/skills/ws_victim/private_cap", params={"workspace_id": ws_probe}
    )
    missing_group = probe.get(
        "/api/agent-catalog/skills/no_such_group/whatever", params={"workspace_id": ws_probe}
    )
    assert existing_ws.status_code == 404
    assert missing_group.status_code == 404
    assert existing_ws.json()["detail"] == missing_group.json()["detail"]
    # A real group directory through the member's own scope still reads.
    _make_skill_repo(base / "real-group/shared_cap")
    assert (
        probe.get(
            "/api/agent-catalog/skills/real-group/shared_cap", params={"workspace_id": ws_probe}
        ).status_code
        == 200
    )


def test_sibling_endpoints_reject_empty_workspace_id(client, job_db) -> None:
    """#745 follow-up (R9 P3-2): the sibling query-scoped endpoints share the
    min_length=1 contract — an empty workspace_id is a 422, not a silently
    skipped membership check."""
    _create_member(client, "sibling-probe", "pw-sibling")
    probe = _member_client(client, "sibling-probe", "pw-sibling")
    assert probe.get("/api/agent-catalog?workspace_id=").status_code == 422
    assert probe.get("/api/skills/directories?workspace_id=").status_code == 422
