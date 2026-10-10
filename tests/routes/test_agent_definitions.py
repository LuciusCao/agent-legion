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
    return job_db.create_workspace("Agent Routes WS")["id"]


@pytest.fixture
def ws(workspace_id) -> dict[str, str]:
    """Every catalog endpoint takes the required workspace_id query parameter."""
    return {"workspace_id": workspace_id}


def _publish(client, agent_id: str, ws: dict[str, str]):
    """#841: publish requires expected_hash — assert the current draft's hash
    (read back from the detail endpoint, as the inspector panel does)."""
    draft_hash = client.get(f"{BASE}/{agent_id}", params=ws).json()["latest"]["definition_hash"]
    return client.post(f"{BASE}/{agent_id}/publish", params=ws, json={"expected_hash": draft_hash})


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

    published = _publish(client, "agent-a", ws)
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["published_at"] is not None

    detail = client.get(f"{BASE}/agent-a", params=ws).json()
    assert detail["published"]["version"] == 1


# #407：创建表单不再收集 agent_id——省略时服务端按 capability 生成实体键。
def test_create_without_agent_id_uses_capability(client, ws) -> None:
    created = client.post(BASE, params=ws, json=PAYLOAD_V1)
    assert created.status_code == 200
    assert created.json()["agent_id"] == "review_keywords"
    assert created.json()["definition"]["capability"] == "review_keywords"

    # agent_id: null 与省略等价（契约层 Optional）——用别的 capability 验证
    # （同 capability 再建会撞占用检查，见下个测试）。
    other_cap = {
        "capability": "generate_questions",
        "runtime": "velites",
        "skill": "demo_workflow/generate_questions",
    }
    explicit_null = client.post(BASE, params=ws, json={"agent_id": None, **other_cap})
    assert explicit_null.status_code == 200
    assert explicit_null.json()["agent_id"] == "generate_questions"


def test_create_without_agent_id_conflicts_on_existing_entity(client, ws) -> None:
    """同 capability 已有实体（草稿或归档）时拒绝隐式创建，引导直接编辑。"""
    client.post(BASE, params=ws, json=PAYLOAD_V1)  # capability review_keywords → 草稿

    conflict = client.post(BASE, params=ws, json=PAYLOAD_V1)
    assert conflict.status_code == 409
    assert "review_keywords" in conflict.json()["detail"]
    assert "请直接编辑" in conflict.json()["detail"]

    # 归档后实体仍存在：隐式创建同样 409（不复活、不加后缀）。
    client.delete(f"{BASE}/review_keywords", params=ws)
    archived_conflict = client.post(BASE, params=ws, json=PAYLOAD_V1)
    assert archived_conflict.status_code == 409

    # 显式 agent_id 不走冲突检查，保持旧语义（另建变体草稿的逃生口）。
    variant = client.post(BASE, params=ws, json={"agent_id": "agent-b", **PAYLOAD_V1})
    assert variant.status_code == 200
    assert variant.json()["agent_id"] == "agent-b"


def test_create_without_agent_id_conflicts_on_published_row_hidden_by_draft(client, ws) -> None:
    """#460 P1：published v1（capability A）与改 capability 的草稿 v2 并存时，
    缺省创建 A 仍 409——修复前放行，静默覆盖 v2 草稿（旧实体 id 恰为 A）或
    建出无法发布的新实体（legacy id）。"""
    client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
    _publish(client, "agent-a", ws)
    renamed = {**PAYLOAD_V1, "capability": "renamed_cap"}
    client.put(f"{BASE}/agent-a/draft", params=ws, json=renamed)  # v2 草稿：capability 变更

    conflict = client.post(BASE, params=ws, json=PAYLOAD_V1)
    assert conflict.status_code == 409
    assert "状态：published" in conflict.json()["detail"]
    assert "agent-a" in conflict.json()["detail"]
    assert "请直接编辑" in conflict.json()["detail"]

    # v2 草稿（renamed capability）与 published v1 均原样保留。
    detail = client.get(f"{BASE}/agent-a", params=ws).json()
    assert detail["latest"]["status"] == "draft"
    assert detail["latest"]["definition"]["capability"] == "renamed_cap"
    assert detail["published"]["definition"]["capability"] == "review_keywords"

    # 同键静默覆盖分支（占用检查第三面）：实体 id 恰为 capability、但其草稿
    # 改了 capability 且无 published 行——按实体键命中 409，不覆盖该草稿。
    other = {**PAYLOAD_V1, "capability": "other_cap"}
    client.post(BASE, params=ws, json={"agent_id": "same_key_cap", **other})
    same_key_target = {**PAYLOAD_V1, "capability": "same_key_cap"}
    key_conflict = client.post(BASE, params=ws, json={"agent_id": None, **same_key_target})
    assert key_conflict.status_code == 409
    assert "状态：draft" in key_conflict.json()["detail"]
    assert "same_key_cap" in key_conflict.json()["detail"]


def test_workspace_id_required(client) -> None:
    assert client.get(BASE).status_code == 422
    assert client.post(BASE, json={"agent_id": "agent-a", **PAYLOAD_V1}).status_code == 422


def test_catalogs_are_workspace_isolated(client, job_db, ws, workspace_id) -> None:
    client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
    _publish(client, "agent-a", ws)

    other = job_db.create_workspace("Other WS")["id"]
    listed = client.get(BASE, params={"workspace_id": other})
    assert listed.status_code == 200
    assert listed.json()["agents"] == []
    assert client.get(f"{BASE}/agent-a", params={"workspace_id": other}).status_code == 404


def test_catalog_is_admin_only_for_non_admin(client, job_db, ws, workspace_id) -> None:
    """非 admin 鉴权边界（P4：Agent catalog 属 Studio 面，admin-only）：
    A 的 editor 读写 A 的 catalog 一律 403；访问 B（非成员）仍 404
    （require_workspace_access 先跑，存在性不可枚举）。"""
    client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
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

    # 本 workspace（editor 成员）：读写一律 403（Studio 面 admin-only）。
    assert member.get(BASE, params=ws).status_code == 403
    write_own = member.post(BASE, params=ws, json={"agent_id": "agent-y", **PAYLOAD_V1})
    assert write_own.status_code == 403
    # 别的 workspace：读 404，写 404（存在性不可枚举）。
    assert member.get(BASE, params={"workspace_id": other}).status_code == 404
    write = member.post(
        BASE, params={"workspace_id": other}, json={"agent_id": "agent-x", **PAYLOAD_V1}
    )
    assert write.status_code == 404


def test_list_shows_latest_per_agent(client, ws) -> None:
    client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
    _publish(client, "agent-a", ws)
    client.put(f"{BASE}/agent-a/draft", params=ws, json=PAYLOAD_V2)

    listed = client.get(BASE, params=ws)
    assert listed.status_code == 200
    agents = {item["agent_id"]: item for item in listed.json()["agents"]}
    assert agents["agent-a"]["status"] == "draft"
    assert agents["agent-a"]["has_draft"] is True
    assert agents["agent-a"]["version"] == 2
    assert agents["agent-a"]["capability"] == "review_keywords"
    assert agents["agent-a"]["published_capability"] == "review_keywords"
    assert agents["agent-a"]["published_version"] == 1


def test_list_exposes_published_capability_behind_a_draft(client, ws) -> None:
    """#906: published capability A + draft capability B — the list row is the
    draft (B), but the routed published capability (A) is exposed so the
    settings catalog judges references by it; draft-only rows carry null."""
    client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
    _publish(client, "agent-a", ws)
    client.put(f"{BASE}/agent-a/draft", params=ws, json={**PAYLOAD_V1, "capability": "renamed_cap"})
    client.post(BASE, params=ws, json={**PAYLOAD_V1, "agent_id": "agent-d", "capability": "d"})

    listed = client.get(BASE, params=ws)
    assert listed.status_code == 200
    agents = {item["agent_id"]: item for item in listed.json()["agents"]}
    assert agents["agent-a"]["status"] == "draft"
    assert agents["agent-a"]["capability"] == "renamed_cap"
    assert agents["agent-a"]["published_capability"] == "review_keywords"
    assert agents["agent-a"]["published_version"] == 1
    assert agents["agent-d"]["status"] == "draft"
    assert agents["agent-d"]["published_capability"] is None
    assert agents["agent-d"]["published_version"] is None


def test_versions_and_rollback(client, ws) -> None:
    client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
    _publish(client, "agent-a", ws)
    client.put(f"{BASE}/agent-a/draft", params=ws, json=PAYLOAD_V2)
    _publish(client, "agent-a", ws)

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


def test_publish_requires_expected_hash_and_rejects_stale(client, ws) -> None:
    """#841：hash-less 发布退役——缺 body/字段 422；旧 hash 409 零副作用。"""
    client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
    assert client.post(f"{BASE}/agent-a/publish", params=ws).status_code == 422
    assert client.post(f"{BASE}/agent-a/publish", params=ws, json={}).status_code == 422
    stale = client.post(f"{BASE}/agent-a/publish", params=ws, json={"expected_hash": "stale"})
    assert stale.status_code == 409
    assert "draft hash mismatch" in stale.json()["detail"]
    detail = client.get(f"{BASE}/agent-a", params=ws).json()
    assert detail["published"] is None
    assert detail["latest"]["status"] == "draft"


def test_publish_rejects_duplicate_capability(client, ws) -> None:
    client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
    _publish(client, "agent-a", ws)
    client.post(BASE, params=ws, json={"agent_id": "agent-b", **PAYLOAD_V1})

    conflict = _publish(client, "agent-b", ws)
    assert conflict.status_code == 409


def test_copy_creates_draft(client, ws) -> None:
    client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
    _publish(client, "agent-a", ws)

    copied = client.post(f"{BASE}/agent-a/copy", params=ws, json={"new_agent_id": "agent-b"})
    assert copied.status_code == 200
    body = copied.json()
    assert body["agent_id"] == "agent-b"
    assert body["version"] == 1
    assert body["status"] == "draft"

    missing = client.post(f"{BASE}/agent-missing/copy", params=ws, json={"new_agent_id": "agent-c"})
    assert missing.status_code == 404


def test_agent_id_charset_rejects_executor_id_form_collision(client, ws) -> None:
    """#1167 评审 P3-1：agent_id 字符域契约——``agent:<id>`` 形态里 ``:`` 是
    形态分隔符，命名为 ``code:x`` 的 agent 会写出 ``agent:code:x`` 租约、
    命中节点限额计数的 ``agent:code:%`` 前缀（agent 车道租约消耗 code
    额度，#1167 症状回流）。创建与复制两个新键入口（AgentCreateRequest /
    AgentCopyRequest）都按 ``AGENT_ID_RE`` 拒绝含 ``:`` 及其他越域形态
    （422 契约层拦截，零落库）；合法字符域内形态照常放行。存量
    pre-constraint 命名不迁移（口径表边界行如实记录，行为钉子见
    tests/db/test_claim_node_limit_remote.py 用例 11）。"""
    for bad in ("code:x", ":x", "a:b", "agent-b-c:extra", " agent", "agent ", "名", "a/b"):
        rejected = client.post(BASE, params=ws, json={"agent_id": bad, **PAYLOAD_V1})
        assert rejected.status_code == 422, (bad, rejected.text)

    created = client.post(BASE, params=ws, json={"agent_id": "agent-ok.v2_x", **PAYLOAD_V1})
    assert created.status_code == 200
    assert created.json()["agent_id"] == "agent-ok.v2_x"

    client.post(BASE, params=ws, json={"agent_id": "agent-src", **PAYLOAD_V2})
    _publish(client, "agent-src", ws)
    bad_copy = client.post(f"{BASE}/agent-src/copy", params=ws, json={"new_agent_id": "code:y"})
    assert bad_copy.status_code == 422
    ok_copy = client.post(f"{BASE}/agent-src/copy", params=ws, json={"new_agent_id": "agent-cpy1"})
    assert ok_copy.status_code == 200
    assert ok_copy.json()["agent_id"] == "agent-cpy1"


# #1173 codex 二轮（codex finding：契约层只封了两个请求字段，其余三条
# 受支持路径可产生 ``code:x`` 形态 id）：根因修法 = 校验下沉 service 写边界，
# 下述用例钉住剩余路径——capability 派生、PUT 路径参数、存量兼容。
def test_create_derivation_rejects_capability_outside_agent_id_charset(client, ws) -> None:
    """路径② capability 派生：``code:x`` 形态 capability 派生的 agent_id 撞
    executor_id 形态前缀（#1167）——service 写边界拒绝（400）并引导显式
    指定合法 agent_id；同 capability 显式合法 id 照常创建（capability 不收紧）。"""
    derived = client.post(BASE, params=ws, json={"capability": "code:x", "runtime": "velites"})
    assert derived.status_code == 400, derived.text
    assert "显式指定合法 agent_id" in derived.json()["detail"]
    assert "code:x" in derived.json()["detail"]

    explicit = client.post(
        BASE,
        params=ws,
        json={"agent_id": "agent-legal", "capability": "code:x", "runtime": "velites"},
    )
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()["agent_id"] == "agent-legal"
    assert explicit.json()["definition"]["capability"] == "code:x"


def test_put_draft_path_param_rejects_new_illegal_agent_id(client, ws) -> None:
    """路径③ PUT 路径参数：新实体的 ``code:x`` 键在 service 写边界被拒
    （400，InvalidOperationError 语义），零落库。"""
    saved = client.put(f"{BASE}/code:x/draft", params=ws, json=PAYLOAD_V1)
    assert saved.status_code == 400, saved.text
    assert "不在合法字符域" in saved.json()["detail"]
    assert client.get(f"{BASE}/code:x", params=ws).status_code == 404


def test_stock_illegal_agent_id_reads_edits_and_publishes_in_place(client, job_db, ws) -> None:
    """存量兼容（#1173 选型「新建不允许、原位更新放行」）：直写预置的
    pre-constraint 非常规键——读取、原位编辑、发布照常，不被写边界门锁死
    （存量 Agent 的迁移路径保持可用）。"""
    import json as _json

    canonical = _json.dumps(PAYLOAD_V1, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with job_db.connect() as conn:  # 写面门之外的直写预置（存量形态的来路）
        conn.execute(
            "insert into versioned_entities("
            "id, entity_type, workspace_id, entity_key, version, status,"
            " definition_json, definition_hash, created_by)"
            " values ('agent:legacy:code-x:v1', 'agent', %s, 'code:x', 1, 'draft',"
            " %s, 'legacy-hash', 'legacy-seed')",
            (ws["workspace_id"], canonical),
        )

    assert client.get(f"{BASE}/code:x", params=ws).status_code == 200  # 读取不受影响

    saved = client.put(f"{BASE}/code:x/draft", params=ws, json=PAYLOAD_V2)  # 原位更新放行
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1

    published = _publish(client, "code:x", ws)
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"


def test_archive_all(client, ws) -> None:
    client.post(BASE, params=ws, json={"agent_id": "agent-a", **PAYLOAD_V1})
    _publish(client, "agent-a", ws)

    archived = client.delete(f"{BASE}/agent-a", params=ws)
    assert archived.status_code == 200
    assert archived.json()["archived"] == 1

    detail = client.get(f"{BASE}/agent-a", params=ws).json()
    assert detail["published"] is None
    assert detail["latest"]["status"] == "archived"


def test_unknown_agent_404(client, ws) -> None:
    assert client.get(f"{BASE}/agent-missing", params=ws).status_code == 404
    assert client.get(f"{BASE}/agent-missing/versions", params=ws).status_code == 404
    missing = client.post(f"{BASE}/agent-missing/publish", params=ws, json={"expected_hash": "x"})
    assert missing.status_code == 404


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


def test_skill_is_optional(client, ws) -> None:
    """#76: skill 降为可选 legacy 兜底——缺省/空串合法，定义 skill 为 ""。"""
    omitted = {k: v for k, v in PAYLOAD_V1.items() if k != "skill"}
    created = client.post(BASE, params=ws, json={"agent_id": "agent-bare", **omitted})
    assert created.status_code == 200
    assert created.json()["definition"]["skill"] == ""

    empty = client.post(
        BASE, params=ws, json={"agent_id": "agent-empty", **PAYLOAD_V1, "skill": ""}
    )
    assert empty.status_code == 200
    assert empty.json()["definition"]["skill"] == ""

    listed = client.get(BASE, params=ws).json()["agents"]
    assert next(a for a in listed if a["agent_id"] == "agent-bare")["skill"] == ""


def test_write_endpoints_are_deprecated_and_reads_are_not(client, ws) -> None:
    """#935 (#440 P3, D3): every write endpoint answers with a Deprecation
    header pointing authors at node execution profiles and is flagged
    deprecated in OpenAPI; read endpoints stay plain."""
    created = client.post(BASE, params=ws, json={"agent_id": "agent-d", **PAYLOAD_V1})
    saved = client.put(f"{BASE}/agent-d/draft", params=ws, json=PAYLOAD_V2)
    published = _publish(client, "agent-d", ws)
    copied = client.post(f"{BASE}/agent-d/copy", params=ws, json={"new_agent_id": "agent-e"})
    rolled = client.post(f"{BASE}/agent-d/rollback", params=ws, json={"version": 1})
    archived = client.delete(f"{BASE}/agent-e", params=ws)
    for response in (created, saved, published, copied, rolled, archived):
        assert response.status_code == 200, response.text
        assert response.headers["Deprecation"] == "true"
        assert "execution.runtime" in response.headers["X-Agent-Legion-Deprecation"]
    for read in (client.get(BASE, params=ws), client.get(f"{BASE}/agent-d", params=ws)):
        assert read.status_code == 200
        assert "Deprecation" not in read.headers

    paths = client.app.openapi()["paths"]
    assert paths["/api/agent-definitions"]["post"]["deprecated"] is True
    assert paths["/api/agent-definitions/{agent_id}/publish"]["post"]["deprecated"] is True
    assert not paths["/api/agent-definitions"]["get"].get("deprecated", False)
