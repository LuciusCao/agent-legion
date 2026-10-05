"""Runs API × item 级 client_token（#813）与 text 条目 ``.json``。

- 同内容不同 token → 各自独立 job；同 token 重复提交 → 幂等命中同一 job；
- 不传 token → 纯内容寻址，job id / run digest 与 #813 之前逐字节一致（钉子）；
- material / bundle / text 统一支持，ref 拒收（已有 connection_key:external_id 命名空间）；
- 非法 token 在契约层 422、服务层 400；API token 通道与会话通道行为一致；
- text 条目 ``.json`` 文件名落盘 ``application/json``，未 opt-in text 的契约仍 fail-closed。
"""

from __future__ import annotations

import copy
import hashlib

import pytest

from tests.fakes.storage import FakeObjectStorage

WORKFLOW_KEY = "education_video_problems_generation"


@pytest.fixture
def storage(client, monkeypatch) -> FakeObjectStorage:
    fake = FakeObjectStorage()
    monkeypatch.setattr(client.app.state.materials_service, "storage", fake)
    return fake


def _create_workspace(client, accepted: list[str] | None = None) -> str:
    response = client.post("/api/workspaces", json={"id": WORKFLOW_KEY, "name": "token-ws"})
    assert response.status_code == 200, response.text
    from tests.helpers import publish_builtin_revision

    publish_builtin_revision(client.app.state.job_db, WORKFLOW_KEY)
    if accepted is not None:
        from server.app.services.workflow_revisions import WorkflowRevisionService
        from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
        from server.app.workflows.definition import workflow_definition_from_dict

        raw = copy.deepcopy(DEMO_WORKFLOW_DEFINITION)
        raw["nodes"]["_start"]["accepted_item_types"] = accepted
        WorkflowRevisionService(client.app.state.job_db).publish_workspace_revision(
            WORKFLOW_KEY, workflow_definition_from_dict(raw)
        )
    return WORKFLOW_KEY


def _insert_ready_material(job_db, workspace_id: str, material_id: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by)"
            " values (%s, %s, %s, 'doc.txt', 'text/plain', 10, %s, 'ready', 'tester')",
            (
                material_id,
                workspace_id,
                f"hash-{material_id}",
                f"{workspace_id}/hash-{material_id}/doc.txt",
            ),
        )


def _create_run(client, workspace_id: str, items: list[dict]):
    return client.post(f"/api/workspaces/{workspace_id}/runs", json={"items": items})


def _job_id(source_id: str) -> str:
    return f"{WORKFLOW_KEY}_{WORKFLOW_KEY}_{source_id}"


def _jobs(job_db, workspace_id: str) -> dict[str, dict]:
    with job_db.read() as conn:
        rows = conn.execute(
            "select id, source_type, source_id, run_id, input_json from jobs where workspace_id=%s",
            (workspace_id,),
        ).fetchall()
    return {str(row["id"]): dict(row) for row in rows}


# --- 不传 token：既有身份派生不漂移（钉子） -----------------------------------


def test_deterministic_run_id_formula_is_pinned() -> None:
    """run id 的 digest 公式钉死：token-less 提交的 digest 输入不变，id 就不变。"""
    from server.app.jobs.queries.run_healing import deterministic_run_id

    payload = {
        "workflow_key": "wf",
        "source_kind": "items",
        "items": [{"type": "material", "material_id": "mat-a"}],
        "node_config": {},
    }
    assert deterministic_run_id("ws", "wf", "items", payload) == "ws_wf_items_654337df5384beda"


def test_tokenless_submission_keeps_job_id_and_digest_payload(client, job_db, monkeypatch) -> None:
    workspace_id = _create_workspace(client)
    _insert_ready_material(job_db, workspace_id, "mat-a")
    captured: list[dict] = []
    app_db = client.app.state.job_db
    original = app_db.create_run

    def spy(workflow_key, source_kind, digest_payload, **kwargs):
        captured.append(copy.deepcopy(digest_payload))
        return original(workflow_key, source_kind, digest_payload, **kwargs)

    monkeypatch.setattr(app_db, "create_run", spy)

    response = _create_run(client, workspace_id, [{"type": "material", "material_id": "mat-a"}])

    assert response.status_code == 200, response.text
    # Job id = workspace_workflow_materialid: no token suffix, no new key.
    assert response.json()["job_ids"] == [_job_id("mat-a")]
    (job,) = _jobs(job_db, workspace_id).values()
    assert (job["source_type"], job["source_id"]) == ("material", "mat-a")
    # The digest sees the items verbatim — no client_token key materializes.
    (payload,) = captured
    assert payload["items"] == [{"type": "material", "material_id": "mat-a"}]
    from server.app.jobs.queries.run_healing import deterministic_run_id

    assert response.json()["run"]["id"] == deterministic_run_id(
        workspace_id, WORKFLOW_KEY, "items", payload
    )


def test_max_length_token_keeps_job_id_a_valid_dir_name() -> None:
    """job id 兼作存储目录名（≤255 字节）：最长 workspace/workflow + 32 位 id + 最长 token 仍放得下。"""
    from server.app.services.run_item_client_token import CLIENT_TOKEN_MAX_CHARS

    longest = f"{'w' * 64}_{'w' * 64}_{'0' * 32}~{'t' * CLIENT_TOKEN_MAX_CHARS}"
    assert len(longest.encode("utf-8")) <= 255


@pytest.mark.parametrize("item_type", ["material", "text"])
def test_null_client_token_equals_omitted(client, storage, job_db, item_type) -> None:
    """显式 ``"client_token": null`` 与省略同一 job 身份、同一 run id（digest 前剥离）。"""
    workspace_id = _create_workspace(client, ["material", "text"])
    _insert_ready_material(job_db, workspace_id, "mat-a")
    item = (
        {"type": "material", "material_id": "mat-a"}
        if item_type == "material"
        else {"type": "text", "content": "# 需求\n"}
    )
    first = _create_run(client, workspace_id, [{**item, "client_token": None}])
    assert first.status_code == 200, first.text
    run_id = first.json()["run"]["id"]
    # Simulate the #501 partial-failure state, then retry with the field omitted:
    # the heal path only finds the row when both spellings share one digest.
    with job_db.write() as conn:
        conn.execute("update runs set status='failed' where id=%s", (run_id,))
    retry = _create_run(client, workspace_id, [item])
    assert retry.status_code == 200, retry.text
    assert retry.json()["run"]["id"] == run_id
    assert retry.json()["created_count"] == 0
    assert retry.json()["run"]["status"] == "created"


# --- material ---------------------------------------------------------------


def test_material_tokens_split_and_retry_idempotently(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    _insert_ready_material(job_db, workspace_id, "mat-a")
    item = {"type": "material", "material_id": "mat-a"}

    first = _create_run(
        client,
        workspace_id,
        [
            {**item, "client_token": "order-1"},
            {**item, "client_token": "order-2"},
            # Intra-request duplicate of order-1 dedups like a pre-existing job.
            {**item, "client_token": "order-1"},
            item,
        ],
    )

    assert first.status_code == 200, first.text
    assert first.json()["job_ids"] == [
        _job_id("mat-a~order-1"),
        _job_id("mat-a~order-2"),
        _job_id("mat-a"),
    ]
    jobs = _jobs(job_db, workspace_id)
    for job in jobs.values():
        # The input document stays the plain material shape (downstream,
        # TTL references and worker materialization are unchanged).
        assert '"client_token"' not in str(job["input_json"])
        assert '"material_id": "mat-a"' in str(job["input_json"])

    # Same token resubmitted → same job, nothing new.
    retry = _create_run(client, workspace_id, [{**item, "client_token": "order-1"}])
    assert retry.status_code == 400
    assert "No tasks were resolved" in retry.json()["detail"]
    # A fresh token over the same content → one more independent job, in a
    # distinct run (the token is part of the run digest).
    third = _create_run(client, workspace_id, [{**item, "client_token": "order-3"}])
    assert third.status_code == 200, third.text
    assert third.json()["job_ids"] == [_job_id("mat-a~order-3")]
    assert third.json()["run"]["id"] != first.json()["run"]["id"]
    assert len(_jobs(job_db, workspace_id)) == 4


def test_token_runs_have_distinct_digests(client, job_db) -> None:
    """不同 token 的 run 是不同的 digest，不会互相命中 #501 治愈路径。"""
    workspace_id = _create_workspace(client)
    _insert_ready_material(job_db, workspace_id, "mat-a")
    ids = set()
    for token in ("t1", "t2"):
        response = _create_run(
            client,
            workspace_id,
            [{"type": "material", "material_id": "mat-a", "client_token": token}],
        )
        assert response.status_code == 200, response.text
        ids.add(response.json()["run"]["id"])
    assert len(ids) == 2


# --- text --------------------------------------------------------------------


def test_text_tokens_share_material_but_split_jobs(client, storage, job_db) -> None:
    workspace_id = _create_workspace(client, ["material", "text"])
    text = {"type": "text", "content": "# 同一份需求\n"}

    first = _create_run(
        client,
        workspace_id,
        [{**text, "client_token": "ext-1"}, {**text, "client_token": "ext-2"}],
    )

    assert first.status_code == 200, first.text
    materials = client.get(f"/api/workspaces/{workspace_id}/materials").json()["materials"]
    (material,) = materials  # content-addressed: one material for both
    assert first.json()["job_ids"] == [
        _job_id(f"{material['id']}~ext-1"),
        _job_id(f"{material['id']}~ext-2"),
    ]
    retry = _create_run(client, workspace_id, [{**text, "client_token": "ext-2"}])
    assert retry.status_code == 400
    assert "No tasks were resolved" in retry.json()["detail"]
    # Token-less text keeps pure content addressing: its own (unscoped) job.
    plain = _create_run(client, workspace_id, [text])
    assert plain.status_code == 200, plain.text
    assert plain.json()["job_ids"] == [_job_id(material["id"])]
    assert len(storage.objects) == 1


# --- bundle ------------------------------------------------------------------


def _ready_bundle(client, storage, workspace_id: str) -> str:
    payload = b"token-bundle-member"
    content_hash = hashlib.sha256(payload).hexdigest()
    response = client.post(
        f"/api/workspaces/{workspace_id}/materials/presign",
        json={
            "filename": "a.txt",
            "size_bytes": len(payload),
            "content_type": "text/plain",
            "content_hash": content_hash,
        },
    )
    assert response.status_code == 200, response.text
    material_id = response.json()["material"]["id"]
    storage.objects[f"{workspace_id}/{content_hash}/a.txt"] = payload
    complete = client.post(f"/api/workspaces/{workspace_id}/materials/{material_id}/complete")
    assert complete.status_code == 200, complete.text
    response = client.post(
        f"/api/workspaces/{workspace_id}/material-bundles",
        json={"name": "folder", "members": [{"material_id": material_id, "path": "a.txt"}]},
    )
    assert response.status_code == 200, response.text
    return response.json()["bundle"]["id"]


def test_bundle_tokens_split_jobs(client, storage, job_db) -> None:
    workspace_id = _create_workspace(client, ["material", "bundle"])
    bundle_id = _ready_bundle(client, storage, workspace_id)
    item = {"type": "bundle", "bundle_id": bundle_id}

    response = _create_run(
        client, workspace_id, [{**item, "client_token": "a"}, {**item, "client_token": "b"}]
    )

    assert response.status_code == 200, response.text
    assert response.json()["job_ids"] == [
        _job_id(f"{bundle_id}~a"),
        _job_id(f"{bundle_id}~b"),
    ]
    assert {job["source_type"] for job in _jobs(job_db, workspace_id).values()} == {"bundle"}


# --- negative ------------------------------------------------------------------


@pytest.mark.parametrize(
    "item",
    [
        {"type": "material", "material_id": "mat-a", "client_token": ""},
        {"type": "material", "material_id": "mat-a", "client_token": "x" * 65},
        {"type": "material", "material_id": "mat-a", "client_token": "a/b"},
        {"type": "material", "material_id": "mat-a", "client_token": "a~b"},
        {"type": "material", "material_id": "mat-a", "client_token": "-lead"},
        {"type": "material", "material_id": "mat-a", "client_token": "中文"},
        {"type": "material", "material_id": "mat-a", "client_token": 7},
        {"type": "text", "content": "x", "client_token": "a b"},
        # ref items are already caller-namespaced: the field is not accepted.
        {"type": "ref", "connection_key": "cms", "external_id": "1", "client_token": "a"},
    ],
)
def test_invalid_client_token_is_rejected_before_any_write(client, storage, job_db, item) -> None:
    workspace_id = _create_workspace(client, ["material", "text", "ref"])
    _insert_ready_material(job_db, workspace_id, "mat-a")

    response = _create_run(client, workspace_id, [item])

    assert response.status_code == 422, response.text
    assert client.get(f"/api/workspaces/{workspace_id}/runs").json()["runs"] == []
    assert _jobs(job_db, workspace_id) == {}
    assert storage.objects == {}


@pytest.mark.parametrize(
    ("item", "detail"),
    [
        ({"type": "material", "material_id": "m", "client_token": "a~b"}, "client_token must"),
        ({"type": "material", "material_id": "m", "client_token": "ok\n"}, "client_token must"),
        ({"type": "bundle", "bundle_id": "b", "client_token": ""}, "client_token must"),
        (
            {"type": "ref", "connection_key": "c", "external_id": "e", "client_token": "a"},
            "not supported on 'ref'",
        ),
    ],
)
def test_service_layer_rejects_invalid_client_token(item, detail) -> None:
    """Direct service callers (no contract model) get the same verdict as 400."""
    from server.app.services.job_errors import InvalidOperationError
    from server.app.services.run_item_resolution import resolve_run_items

    with pytest.raises(InvalidOperationError, match=detail):
        resolve_run_items(object(), "ws", [item])


# --- API token channel ------------------------------------------------------------


def test_api_token_channel_matches_session_channel(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    _insert_ready_material(job_db, workspace_id, "mat-a")
    issued = client.post(f"/api/workspaces/{workspace_id}/api-tokens", json={"label": "cms"})
    assert issued.status_code == 201, issued.text
    api = client.__class__(client.app)
    api.headers["authorization"] = f"Bearer {issued.json()['api_token']}"
    item = {"type": "material", "material_id": "mat-a"}

    by_api = _create_run(api, workspace_id, [{**item, "client_token": "ext-1"}])
    assert by_api.status_code == 200, by_api.text
    assert by_api.json()["job_ids"] == [_job_id("mat-a~ext-1")]
    # The session channel's retry of the same token hits the same job.
    by_session = _create_run(client, workspace_id, [{**item, "client_token": "ext-1"}])
    assert by_session.status_code == 400
    assert "No tasks were resolved" in by_session.json()["detail"]
    # And the contract verdict is channel-independent.
    bad = _create_run(api, workspace_id, [{**item, "client_token": "a/b"}])
    assert bad.status_code == 422


# --- text .json ---------------------------------------------------------------------


def test_text_item_json_filename_stores_json_content_type(client, storage, job_db) -> None:
    workspace_id = _create_workspace(client, ["material", "text"])

    response = _create_run(
        client,
        workspace_id,
        [{"type": "text", "content": '{"song": "x"}', "filename": "payload.JSON"}],
    )

    assert response.status_code == 200, response.text
    (material,) = client.get(f"/api/workspaces/{workspace_id}/materials").json()["materials"]
    assert material["filename"] == "payload.JSON"
    assert material["content_type"] == "application/json; charset=utf-8"


def test_start_node_json_default_filename_publishes_and_applies(client, storage, job_db) -> None:
    """start 节点 text_input.filename 可配 ``.json``，作为无 filename 条目的默认落盘名。"""
    from server.app.services.workflow_revisions import WorkflowRevisionService
    from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION
    from server.app.workflows.definition import workflow_definition_from_dict

    workspace_id = _create_workspace(client)
    raw = copy.deepcopy(DEMO_WORKFLOW_DEFINITION)
    raw["nodes"]["_start"]["accepted_item_types"] = ["material", "text"]
    raw["nodes"]["_start"]["text_input"] = {"filename": "payload.json"}
    WorkflowRevisionService(client.app.state.job_db).publish_workspace_revision(
        workspace_id, workflow_definition_from_dict(raw)
    )

    response = _create_run(client, workspace_id, [{"type": "text", "content": "{}"}])

    assert response.status_code == 200, response.text
    (material,) = client.get(f"/api/workspaces/{workspace_id}/materials").json()["materials"]
    assert material["filename"] == "payload.json"
    assert material["content_type"] == "application/json; charset=utf-8"


def test_text_item_json_still_fails_closed_without_opt_in(client, storage, job_db) -> None:
    workspace_id = _create_workspace(client)

    response = _create_run(
        client, workspace_id, [{"type": "text", "content": "{}", "filename": "a.json"}]
    )

    assert response.status_code == 400
    assert "not accepted by this workflow" in response.json()["detail"]
    assert storage.objects == {}


# --- #925：job 视图的只读结构化 client_token ---------------------------------


def test_job_views_expose_structured_client_token(client, job_db) -> None:
    """详情 / 列表 / snapshot 的 job 条目带服务端解析的 client_token 与 source_base_id。"""
    workspace_id = _create_workspace(client)
    _insert_ready_material(job_db, workspace_id, "mat-a")
    item = {"type": "material", "material_id": "mat-a"}
    response = _create_run(
        client,
        workspace_id,
        [{**item, "client_token": "order-1"}, {**item, "client_token": "order-2"}, item],
    )
    assert response.status_code == 200, response.text
    expected = {
        _job_id("mat-a~order-1"): "order-1",
        _job_id("mat-a~order-2"): "order-2",
        _job_id("mat-a"): None,
    }

    for job_id, token in expected.items():
        detail = client.get(f"/api/jobs/{job_id}")
        assert detail.status_code == 200, detail.text
        job = detail.json()["job"]
        assert (job["client_token"], job["source_base_id"]) == (token, "mat-a")

    listed = client.get(f"/api/workspaces/{workspace_id}/jobs").json()["jobs"]
    snapshot = client.get(f"/api/workspaces/{workspace_id}/jobs/snapshot").json()["jobs"]
    for jobs in (listed, snapshot):
        assert {job["id"]: job["client_token"] for job in jobs} == expected
        assert {job["source_base_id"] for job in jobs} == {"mat-a"}
