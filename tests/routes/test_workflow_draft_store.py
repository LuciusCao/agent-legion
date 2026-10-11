"""GET/PUT /api/workspaces/{id}/workflow-draft (Studio YAML draft store)."""

from __future__ import annotations

_DRAFT_YAML = "key: wf\nlabel: Draft\nnodes:\n  intake:\n    capability: intake\n"


def _url(workspace_id: str) -> str:
    return f"/api/workspaces/{workspace_id}/workflow-draft"


def test_get_returns_structured_empty_state(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-empty")

    response = client.get(_url(workspace["id"]))

    assert response.status_code == 200
    assert response.json() == {
        "definition_yaml": None,
        "updated_at": None,
        "definition_hash": None,
        "generation": 0,
    }


def test_put_then_get_roundtrip(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store")

    put = client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})

    assert put.status_code == 200
    assert put.json()["definition_yaml"] == _DRAFT_YAML
    assert put.json()["updated_at"]
    got = client.get(_url(workspace["id"]))
    assert got.json()["definition_yaml"] == _DRAFT_YAML
    assert got.json()["updated_at"] == put.json()["updated_at"]


def test_put_then_get_echo_the_semantic_identity_hash(client, job_db) -> None:
    """#1143：draft 存取响应携带语义身份 hash——同一身份在 PUT/GET 间一致，
    语义等价（仅序列化风格不同）的 YAML 产生相同 hash，语义变化产生新
    hash。"""
    from server.app.services.workflow_drafts import workflow_draft_identity_hash

    workspace = job_db.create_workspace("ws-store-hash")
    put = client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})
    expected = workflow_draft_identity_hash(_DRAFT_YAML)
    assert put.status_code == 200
    assert put.json()["definition_hash"] == expected
    got = client.get(_url(workspace["id"]))
    assert got.json()["definition_hash"] == expected

    # 语义等价重排（键序差异）：同一身份。
    reordered = "label: Draft\nkey: wf\nnodes:\n  intake:\n    capability: intake\n"
    reordered_put = client.put(_url(workspace["id"]), json={"definition_yaml": reordered})
    assert reordered_put.json()["definition_hash"] == expected

    # 语义变化：不同身份。
    changed = client.put(
        _url(workspace["id"]),
        json={"definition_yaml": "key: wf\nlabel: V2\n"},
    )
    assert changed.json()["definition_hash"] != expected


def test_unparseable_draft_carries_null_identity(client, job_db) -> None:
    """draft store 允许暂存未通过校验的 YAML：不可解析 → definition_hash
    为 null（前端按「无法核对」降级），不影响存取。"""
    workspace = job_db.create_workspace("ws-store-unparseable")
    put = client.put(_url(workspace["id"]), json={"definition_yaml": "key: [unclosed"})

    assert put.status_code == 200
    assert put.json()["definition_yaml"] == "key: [unclosed"
    assert put.json()["definition_hash"] is None
    assert client.get(_url(workspace["id"])).json()["definition_hash"] is None


def test_put_overwrites_the_previous_draft(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-overwrite")
    client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})

    updated = client.put(_url(workspace["id"]), json={"definition_yaml": "key: wf\nlabel: V2\n"})

    assert updated.status_code == 200
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] == "key: wf\nlabel: V2\n"


def test_put_rejects_blank_draft(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-blank")

    for blank in ("", "   \n  "):
        response = client.put(_url(workspace["id"]), json={"definition_yaml": blank})
        assert response.status_code == 422
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] is None


def test_unknown_workspace_gets_404(client) -> None:
    assert client.get(_url("no-such-ws")).status_code == 404
    put = client.put(_url("no-such-ws"), json={"definition_yaml": _DRAFT_YAML})
    assert put.status_code == 404


def test_drafts_are_isolated_between_workspaces(client, job_db) -> None:
    first = job_db.create_workspace("ws-store-a")
    second = job_db.create_workspace("ws-store-b")

    client.put(_url(first["id"]), json={"definition_yaml": _DRAFT_YAML})

    assert client.get(_url(second["id"])).json() == {
        "definition_yaml": None,
        "updated_at": None,
        "definition_hash": None,
        "generation": 0,
    }


def test_anonymous_gets_401(anon_client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-anon")

    assert anon_client.get(_url(workspace["id"])).status_code == 401
    put = anon_client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})
    assert put.status_code == 401


def test_cookie_put_without_csrf_header_gets_403(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-csrf")
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

    workspace = job_db.create_workspace("ws-store-scoped")
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


# --- #633 codex review P1-1 + #1226 五轮收口（设计 v2）：PUT 的 CAS 基线是
# 草稿代次（base_generation）——唯一不变量「写入声明它基于的代次 G，G
# 落后于当前代次即 409」；缺省 = legacy 无代次 LWW 放行。本节按迁移表逐格
# 钉住（代次匹配 × 行在/行缺，代次落后 × 行在/行缺，legacy 两格）。
#
# 代次推进规则：每次成功写 +1、发布删行 +1；行缺失时 GET 空态也携带当前
# 代次（发布后空态上的首存以它为 base）。


def _generation(client, workspace_id: str) -> int:
    return int(client.get(_url(workspace_id)).json()["generation"])


def test_put_with_matching_base_generation_succeeds(client, job_db) -> None:
    """迁移表「G=g × 行存在」：覆盖写入，代次 +1。"""
    workspace = job_db.create_workspace("ws-store-cas-ok")
    first = client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})
    base = first.json()["generation"]
    assert base == 1

    matched = client.put(
        _url(workspace["id"]),
        json={"definition_yaml": "key: wf\nlabel: V2\n", "base_generation": base},
    )

    assert matched.status_code == 200, matched.text
    assert matched.json()["generation"] == 2
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] == "key: wf\nlabel: V2\n"


def test_put_on_post_publish_empty_state_with_current_generation_inserts(client, job_db) -> None:
    """迁移表「G=g × 行不存在」：读到发布后空态（代次 g）的首存放行
    INSERT——它声明的就是当前代次，不是发布前代次。"""
    workspace = job_db.create_workspace("ws-store-gen-insert")
    first = client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})
    assert first.status_code == 200
    _publish_delete_draft(job_db, workspace["id"])

    empty = client.get(_url(workspace["id"]))
    assert empty.json()["definition_yaml"] is None
    current_generation = int(empty.json()["generation"])
    assert current_generation == 2  # write(+1) + publish delete(+1)

    inserted = client.put(
        _url(workspace["id"]),
        json={"definition_yaml": _DRAFT_YAML, "base_generation": current_generation},
    )
    assert inserted.status_code == 200, inserted.text
    assert inserted.json()["generation"] == 3


def test_put_with_stale_base_generation_gets_409_with_current_draft(client, job_db) -> None:
    """迁移表「G<g × 行存在」：agent 保存的草稿不得被持落后代次的人类
    自动保存静默覆盖——409 的 detail 携带当前草稿（与当前代次），编辑器
    一轮重 base 即可恢复。"""
    workspace = job_db.create_workspace("ws-store-cas-stale")
    first = client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})
    assert first.status_code == 200
    stale_base = int(first.json()["generation"])

    # The agent tool surface (or another session) saves a newer draft.
    agent_yaml = "key: wf\nlabel: Agent v2\n"
    agent_saved = client.put(
        f"/api/studio-agent/tools/workspaces/{workspace['id']}/workflow/draft",
        json={"definition_yaml": agent_yaml, "base_generation": stale_base},
        headers=_admin_bearer(client, job_db),
    )
    assert agent_saved.status_code == 200, agent_saved.text

    # The human editor's PUT with the now-stale base loses the race loudly.
    conflict = client.put(
        _url(workspace["id"]),
        json={"definition_yaml": "key: wf\nlabel: Human edit\n", "base_generation": stale_base},
    )

    assert conflict.status_code == 409, conflict.text
    detail = conflict.json()["detail"]
    assert detail["base_generation"] == stale_base
    assert detail["current_draft"]["definition_yaml"] == agent_yaml
    assert detail["current_draft"]["updated_at"]
    assert detail["current_draft"]["generation"] == stale_base + 1
    # #1143：current_draft 携带语义身份（采用服务端版本的一侧据此恢复
    # savedHash，草稿卡核对不误报）。
    from server.app.services.workflow_drafts import workflow_draft_identity_hash

    assert detail["current_draft"]["definition_hash"] == workflow_draft_identity_hash(agent_yaml)
    # #1177 codex P1/P2：整个 409 响应体与 OpenAPI 契约模型（路由
    # responses= 声明的封套模型）逐字段钉死——FastAPI 原生 handler 的
    # detail 外层也在契约内，服务层 payload 构造与 contracts 模型由本
    # 断言同步，前端 transport type 才能安全从生成的 api.ts 派生。
    from server.app.routes.workflow_draft_store_contracts import (
        WorkflowDraftConflictCurrentDraft as ContractCurrentDraft,
    )
    from server.app.routes.workflow_draft_store_contracts import (
        WorkflowDraftConflictDetail as ContractDetail,
    )
    from server.app.routes.workflow_draft_store_contracts import (
        WorkflowDraftConflictResponse as ContractResponse,
    )

    envelope = ContractResponse.model_validate(conflict.json())
    assert envelope.detail.message
    assert set(conflict.json()) == set(ContractResponse.model_fields)
    assert set(detail) == set(ContractDetail.model_fields)
    assert set(detail["current_draft"]) == set(ContractCurrentDraft.model_fields)
    # The agent-saved draft was NOT overwritten.
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] == agent_yaml

    # Rebasing onto the current generation succeeds.
    rebased = client.put(
        _url(workspace["id"]),
        json={
            "definition_yaml": "key: wf\nlabel: Human edit\n",
            "base_generation": detail["current_draft"]["generation"],
        },
    )
    assert rebased.status_code == 200, rebased.text
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] == (
        "key: wf\nlabel: Human edit\n"
    )


def test_put_without_base_generation_keeps_last_write_wins(client, job_db) -> None:
    """迁移表「legacy 无代次」两格：行存在覆盖写入、行不存在 INSERT——
    兼容放行（设计 v2 记明的残余：无法在发布代次边界受保护），显式 null
    同论。"""
    workspace = job_db.create_workspace("ws-store-cas-none")
    first = client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})
    base = int(first.json()["generation"])

    # A newer draft lands (any writer), then a legacy PUT without the field.
    newer = client.put(
        _url(workspace["id"]),
        json={"definition_yaml": "key: wf\nlabel: Newer\n", "base_generation": base},
    )
    assert newer.status_code == 200
    legacy = client.put(_url(workspace["id"]), json={"definition_yaml": "key: wf\nlabel: Legacy\n"})

    assert legacy.status_code == 200
    assert legacy.json()["definition_yaml"] == "key: wf\nlabel: Legacy\n"
    # The same for an explicit null.
    explicit_null = client.put(
        _url(workspace["id"]),
        json={"definition_yaml": "key: wf\nlabel: Explicit null\n", "base_generation": None},
    )
    assert explicit_null.status_code == 200
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] == (
        "key: wf\nlabel: Explicit null\n"
    )
    # legacy 行不存在格：删除后无 base 的写入照样 INSERT（放行残余）。
    _publish_delete_draft(job_db, workspace["id"])
    legacy_insert = client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})
    assert legacy_insert.status_code == 200


# --- #1226 五轮收口：发布 = 删行 + 代次推进——发布前代次的在途写入 409
# （不再重建旧内容），读到发布后空态的写入按当前代次放行。


def _publish_delete_draft(job_db, workspace_id: str) -> None:
    """What the publish hook does: delete the row + bump the generation."""
    with job_db.connect() as conn:
        job_db.delete_workspace_workflow_draft(conn, workspace_id)


def test_put_with_pre_publish_base_generation_conflicts_without_resurrecting(
    client, job_db
) -> None:
    """迁移表「G<g × 行不存在」：已越过 debounce、发布删行后才到达的在途
    写入——代次落后于发布后的当前代次，409（current_draft 内容全 null，
    前端「仅解除警示」路径），被删内容不得重建。"""
    workspace = job_db.create_workspace("ws-store-gen-stale")
    first = client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})
    assert first.status_code == 200
    pre_publish_base = int(first.json()["generation"])

    _publish_delete_draft(job_db, workspace["id"])

    refused = client.put(
        _url(workspace["id"]),
        json={"definition_yaml": _DRAFT_YAML, "base_generation": pre_publish_base},
    )

    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["base_generation"] == pre_publish_base
    assert detail["current_draft"]["definition_yaml"] is None
    assert detail["current_draft"]["updated_at"] is None
    # 空态的当前代次仍可读（重 base 的落点）。
    assert detail["current_draft"]["generation"] == pre_publish_base + 1
    # The deleted row was NOT resurrected.
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] is None


def test_first_save_after_reading_empty_then_publish_conflicts(client, job_db) -> None:
    """R6：tab B 读到空草稿（代次 0）后，另一写入者保存草稿、随后发布
    （删行 + 代次推进）——B 的首次保存声明的是落后代次，409 而不是把
    「发布前读到的空态内容」写成新草稿。代次判定对 never-saved 场景不再
    豁免：空读的代次也是代次。"""
    workspace = job_db.create_workspace("ws-store-gen-r6")
    empty = client.get(_url(workspace["id"]))
    assert empty.json()["definition_yaml"] is None
    tab_b_base = int(empty.json()["generation"])  # 0

    # 另一写入者建了草稿（gen 1），随后发布删除（gen 2）。
    client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})
    _publish_delete_draft(job_db, workspace["id"])

    refused = client.put(
        _url(workspace["id"]),
        json={"definition_yaml": "key: wf\nlabel: B 的编辑\n", "base_generation": tab_b_base},
    )

    assert refused.status_code == 409, refused.text
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] is None


def test_publish_without_a_draft_row_advances_nothing(client, job_db) -> None:
    """行缺失时不删不进代次（删除是代次边界的载体）——空态首存不受影响。"""
    workspace = job_db.create_workspace("ws-store-gen-noop")

    _publish_delete_draft(job_db, workspace["id"])

    assert _generation(client, workspace["id"]) == 0
    first = client.put(
        _url(workspace["id"]),
        json={"definition_yaml": _DRAFT_YAML, "base_generation": 0},
    )
    assert first.status_code == 200
    assert first.json()["generation"] == 1


# --- base_generation 校验：负数/非整数是 422（输入错误），不进 SQL。


def test_put_with_invalid_base_generation_gets_422(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-cas-bad")

    for bad in (-1, "garbage", "2026-13-45"):
        response = client.put(
            _url(workspace["id"]),
            json={"definition_yaml": _DRAFT_YAML, "base_generation": bad},
        )
        assert response.status_code == 422, bad
    # Nothing was stored.
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] is None


# --- #1226 六轮 codex P1（缺陷族：草稿内容与代次拆两次读）：空态 GET 与
# 409 载荷的「内容 + 代次」必须来自同一快照——拆分读会让并发保存落进两次
# 读之间，产出「内容 null + 并发后的新代次」的错配，客户端据此保存会 CAS
# 成功、静默覆盖从未看到的草稿。


def test_get_empty_state_returns_a_single_consistent_snapshot(client, job_db, monkeypatch) -> None:
    """行读（无草稿）与代次读之间插入一次并发保存：响应的代次必须与 null
    内容同快照（0），而不是并发保存后的 1。"""
    from server.app.jobs import JobQueries

    workspace = job_db.create_workspace("ws-store-atomic-read")
    real_get_generation = JobQueries.get_workspace_workflow_draft_generation

    def interleaved_save(self, workspace_id: str) -> int:
        # 并发交错点：第一次读（无草稿）之后、代次读之时，另一会话落盘。
        self.upsert_workspace_workflow_draft(workspace_id, _DRAFT_YAML)
        return real_get_generation(self, workspace_id)

    monkeypatch.setattr(JobQueries, "get_workspace_workflow_draft_generation", interleaved_save)

    response = client.get(_url(workspace["id"]))

    assert response.status_code == 200
    assert response.json()["definition_yaml"] is None
    # 同快照语义：客户端的首次保存以 0 为 base，撞上并发草稿（代次 1）即
    # 409 重 base，而不是拿 1 静默覆盖。
    assert response.json()["generation"] == 0


def test_conflict_payload_null_content_carries_the_same_snapshot_generation(
    client, job_db, monkeypatch
) -> None:
    """409 的 current_draft 也是「内容 + 代次」对：发布删行后内容 null，
    载荷代次必须是发布推进后的同一快照值（2）——若另读取且中间落进并发
    保存（代次 3），客户端会以未见内容为基线覆盖它。"""
    from server.app.jobs import JobQueries

    workspace = job_db.create_workspace("ws-store-409-atomic")
    client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})  # 代次 1
    _publish_delete_draft(job_db, workspace["id"])  # 删行 + 代次 2

    real_get_generation = JobQueries.get_workspace_workflow_draft_generation

    def interleaved_save(self, workspace_id: str) -> int:
        # 并发交错点：payload 内容读（None）与代次读之间，另一会话落盘。
        self.upsert_workspace_workflow_draft(workspace_id, "key: wf\nlabel: Concurrent\n")
        return real_get_generation(self, workspace_id)

    monkeypatch.setattr(JobQueries, "get_workspace_workflow_draft_generation", interleaved_save)

    conflict = client.put(
        _url(workspace["id"]),
        json={"definition_yaml": _DRAFT_YAML, "base_generation": 1},
    )

    assert conflict.status_code == 409, conflict.text
    current = conflict.json()["detail"]["current_draft"]
    assert current["definition_yaml"] is None
    assert current["generation"] == 2


def _admin_bearer(client, job_db) -> dict[str, str]:
    """Scoped-token Authorization header for the studio-agent tool surface."""
    from server.app.auth import scoped_tokens

    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id)
    return {"authorization": f"Bearer {token}"}
