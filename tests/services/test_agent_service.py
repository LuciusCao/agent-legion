"""AgentService: DB-backed Agent catalog lifecycle on versioned_entities."""

from __future__ import annotations

import json

import pytest

from server.app.agent_catalog import AgentDefinition
from server.app.services.agent_definition_create import create_agent_draft
from server.app.services.agent_service import AgentService
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError

DEFINITION_V1 = AgentDefinition(
    capability="review_keywords", runtime="velites", skill="question/review_key_info"
)
DEFINITION_V2 = AgentDefinition(
    capability="review_keywords",
    runtime="velites",
    skill="question/review_key_info",
    tools=("read",),
)
# #1173 codex 二轮：含 ``:`` 的 capability——派生 id 的越域形态（``:`` 撞
# executor_id ``agent:<id>`` 形态前缀，#1167）。
COLON_CAPABILITY = AgentDefinition(capability="code:x", runtime="velites", skill="q/code")


@pytest.fixture
def workspace_id(job_db) -> str:
    # A fresh workspace per test: capability-uniqueness checks must only see
    # the Agents this test publishes (catalogs are workspace-scoped, v46).
    return job_db.create_workspace("Agent Service WS")["id"]


@pytest.fixture
def service(job_db, workspace_id) -> AgentService:
    return AgentService(job_db.dsn_identity, workspace_id)


def test_save_draft_then_publish_round_trip(service, workspace_id) -> None:
    draft = service.save_draft("agent-a", DEFINITION_V1, "user:u1")

    assert draft.version == 1
    assert draft.status == "draft"
    assert draft.workspace_id == workspace_id
    assert draft.definition_hash == DEFINITION_V1.definition_hash()
    assert service.get_published_definition("agent-a") is None

    published = service.publish("agent-a")

    assert published.status == "published"
    assert published.published_at is not None
    assert service.get_published_definition("agent-a") == DEFINITION_V1


def test_publish_cas_binds_to_the_capability_checked_draft(service, monkeypatch) -> None:
    """#841: the store CAS binds to the draft the capability check ran on —
    an overwrite landing between check and publish is a Conflict with zero
    side effects (before #841 a hash-less publish shipped the unchecked
    newer draft)."""
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    original_check = service._require_free_capability

    def check_then_overwrite(agent_id: str, capability: str) -> None:
        original_check(agent_id, capability)
        service.save_draft("agent-a", DEFINITION_V2, "user:u2")

    monkeypatch.setattr(service, "_require_free_capability", check_then_overwrite)
    with pytest.raises(ConflictError):
        service.publish("agent-a")
    assert service.get_published_definition("agent-a") is None


def test_publish_rejects_mismatched_expected_hash(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    with pytest.raises(ConflictError, match="draft hash mismatch"):
        service.publish("agent-a", DEFINITION_V2.definition_hash())
    assert service.get_published_definition("agent-a") is None
    assert service.publish("agent-a", DEFINITION_V1.definition_hash()).status == "published"


def test_get_published_definition_enforces_hash(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")

    expected = DEFINITION_V1.definition_hash()
    assert service.get_published_definition("agent-a", expected) == DEFINITION_V1
    assert service.get_published_definition("agent-a", "tampered") is None
    assert service.get_published_definition("agent-missing") is None


def test_save_draft_rejects_empty_agent_id(service) -> None:
    with pytest.raises(InvalidOperationError):
        service.save_draft("", DEFINITION_V1, "user:u1")


# #1173 codex 二轮：agent_id 字符域下沉 service 写边界（单一来源
# ``agent_catalog.definition.AGENT_ID_RE``）——新建实体即拒、存量原位放行。
def test_save_draft_rejects_new_entity_id_outside_charset(service) -> None:
    """写边界路径全集的兜底点：显式字段/PUT 路径参数/Studio 端点全部经
    save_draft，非法形态的新实体键在此单点被拒（InvalidOperationError），零落库。"""
    for bad in ("code:x", "a:b", ":x", " leading", "a/b", "名"):
        with pytest.raises(InvalidOperationError, match="不在合法字符域"):
            service.save_draft(bad, DEFINITION_V1, "user:u1")
    assert service.list_latest() == []


def test_save_draft_grandfathers_stock_illegal_id_in_place(service, job_db, workspace_id) -> None:
    """存量兼容（#1173 选型「新建不允许、原位更新放行」）：pre-constraint
    非常规键经 store 层直写预置（store 是通用引擎、不受 AgentService 门
    约束，即存量形态的来路）后，原位编辑、发布、读取照常——不被写边界门
    锁死，存量 Agent 的迁移路径（补 runtime / 调定义后发布）保持可用。"""
    from server.app.services.versioned_entities import VersionedEntityStore

    store = VersionedEntityStore(job_db.dsn_identity, "agent")
    store.save_draft(
        "code:x",
        DEFINITION_V1.model_dump(mode="json"),
        DEFINITION_V1.definition_hash(),
        workspace_id,
        "legacy-seed",
    )

    updated = service.save_draft("code:x", DEFINITION_V2, "user:u1")  # 原位更新放行

    assert updated.entity_key == "code:x"
    assert updated.version == 1  # 覆盖草稿而非另开新版本
    assert updated.created_by == "user:u1"

    published = service.publish("code:x", updated.definition_hash)  # 发布不受影响
    assert published.status == "published"
    assert service.get_published_definition("code:x") == DEFINITION_V2
    assert service.list_versions("code:x")  # 读取不受影响


# #407：创建入口缺省 agent_id——按 capability 生成，占用即冲突
# （create-entry policy 在 agent_definition_create.py，这里经服务对象验证）。
def test_create_draft_derives_agent_id_from_capability(service) -> None:
    entity = create_agent_draft(service, None, DEFINITION_V1, "user:u1")

    assert entity.entity_key == "review_keywords"
    assert entity.status == "draft"
    assert service.get_published_definition("review_keywords") is None


def test_create_draft_derived_id_outside_charset_guides_explicit_agent_id(service) -> None:
    """#1173 codex 二轮（capability 派生路径）：含 ``:`` 的 capability 派生
    id 不合法——显式报错引导（改用合法 capability 命名，或显式指定合法
    agent_id），而不是静默拒 capability（capability 字符域本身不收紧，
    #1173 上轮论证：它是路由/节点声明的语义键，牵连面大）。"""
    with pytest.raises(InvalidOperationError, match="显式指定合法 agent_id") as exc_info:
        create_agent_draft(service, None, COLON_CAPABILITY, "user:u1")
    assert "code:x" in str(exc_info.value)
    assert service.list_latest() == []  # 派生失败零落库

    # capability 本身没被拒：显式合法 agent_id + 同 capability 照常创建。
    entity = create_agent_draft(service, "agent-legal", COLON_CAPABILITY, "user:u1")
    assert entity.entity_key == "agent-legal"
    assert entity.definition["capability"] == "code:x"


def test_create_draft_derivation_conflicts_with_any_existing_entity(service) -> None:
    create_agent_draft(service, None, DEFINITION_V1, "user:u1")  # → review_keywords 草稿

    with pytest.raises(ConflictError, match="review_keywords"):
        create_agent_draft(service, None, DEFINITION_V2, "user:u1")

    # 归档后实体仍在：隐式创建不复活、不加后缀。
    service.archive_all("review_keywords")
    with pytest.raises(ConflictError):
        create_agent_draft(service, None, DEFINITION_V1, "user:u1")


def test_create_draft_derivation_conflicts_with_published_entity(service) -> None:
    """published 状态同样占用 capability：缺省创建 409 且文案标明状态。"""
    create_agent_draft(service, "agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")

    with pytest.raises(ConflictError, match="状态：published") as exc_info:
        create_agent_draft(service, None, DEFINITION_V1, "user:u1")

    # 占用者实体键是 agent-a（显式 id 创建），capability 是 review_keywords。
    assert "agent-a" in str(exc_info.value)
    assert "请直接编辑" in str(exc_info.value)


def test_create_draft_conflicts_with_published_row_hidden_by_newer_draft(service) -> None:
    """#460 P1（codex 精确场景）：实体已发布 v1（capability A）后又保存了
    capability B 的草稿 v2——list_latest 只见 v2，修复前缺省创建 A 会放行，
    且旧实体 id 恰为 A 时 save_draft 静默覆盖用户正在编辑的 v2 草稿。"""
    create_agent_draft(service, "review_keywords", DEFINITION_V1, "user:u1")
    service.publish("review_keywords")  # v1 published：capability review_keywords
    other_cap = AgentDefinition(capability="other_cap", runtime="velites", skill="q/other")
    service.save_draft("review_keywords", other_cap, "user:u1")  # v2 草稿：capability 变更

    with pytest.raises(ConflictError, match="状态：published") as exc_info:
        create_agent_draft(service, None, DEFINITION_V1, "user:u1")

    assert "review_keywords" in str(exc_info.value)
    assert "请直接编辑" in str(exc_info.value)
    # 用户正在编辑的 v2 草稿原样保留（修复前此处被静默覆盖）。
    (draft,) = [e for e in service.list_versions("review_keywords") if e.status == "draft"]
    assert draft.version == 2
    assert draft.definition["capability"] == "other_cap"


def test_create_draft_hidden_published_row_blocks_legacy_id_entity(service) -> None:
    """#460 P1（legacy id 变体）：占用 published 行的实体用其他 legacy id——
    修复前会建出一个因 published-capability 唯一索引而永远无法发布的新实体；
    修复后 409 且文案指向真实占用者。"""
    create_agent_draft(service, "agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")  # v1 published：capability review_keywords
    renamed = AgentDefinition(capability="renamed_cap", runtime="velites", skill="q/renamed")
    service.save_draft("agent-a", renamed, "user:u1")  # v2 草稿：capability 变更

    with pytest.raises(ConflictError, match="状态：published") as exc_info:
        create_agent_draft(service, None, DEFINITION_V1, "user:u1")

    assert "agent-a" in str(exc_info.value)
    assert "请直接编辑" in str(exc_info.value)
    # 不建出以 capability 为键、无法发布的新实体。
    assert all(e.entity_key != "review_keywords" for e in service.list_latest())


def test_create_draft_conflicts_with_same_keyed_entity_whose_draft_renamed(service) -> None:
    """占用检查第三面：实体 id 恰为 capability、最新草稿改成了别的 capability
    且无 published 行——latest/published 两段扫描都不命中，缺省创建仍会
    save_draft 到同键实体上静默覆盖该草稿，必须按实体键命中 409。"""
    other_cap = AgentDefinition(capability="other_cap", runtime="velites", skill="q/other")
    service.save_draft("review_keywords", other_cap, "user:u1")  # 显式 id + 改 capability

    with pytest.raises(ConflictError, match="状态：draft") as exc_info:
        create_agent_draft(service, None, DEFINITION_V1, "user:u1")

    assert "review_keywords" in str(exc_info.value)
    assert "请直接编辑" in str(exc_info.value)
    # 该草稿原样保留。
    (draft,) = [e for e in service.list_versions("review_keywords") if e.status == "draft"]
    assert draft.definition["capability"] == "other_cap"


def test_create_draft_ignores_unrelated_capabilities(service) -> None:
    """占用检查无假阳性：其他实体的 latest 草稿/published 行只按 capability
    命中，无关 capability 的缺省创建照常放行。"""
    create_agent_draft(service, "agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")
    other_cap = AgentDefinition(capability="other_cap", runtime="velites", skill="q/other")
    service.save_draft("agent-a", other_cap, "user:u1")

    third = AgentDefinition(capability="third_cap", runtime="velites", skill="q/third")
    entity = create_agent_draft(service, None, third, "user:u2")

    assert entity.entity_key == "third_cap"
    assert entity.status == "draft"


def test_create_draft_explicit_agent_id_keeps_legacy_semantics(service) -> None:
    """显式 agent_id 不做占用检查：同 key 覆盖草稿（save_draft 旧语义，
    原地覆盖既有草稿行，version 不前进）。"""
    create_agent_draft(service, None, DEFINITION_V1, "user:u1")

    entity = create_agent_draft(service, "review_keywords", DEFINITION_V2, "user:u2")

    assert entity.entity_key == "review_keywords"
    assert entity.version == 1  # 覆盖草稿而不是报错，也不另开新版本
    assert entity.definition["tools"] == ["read"]
    assert entity.created_by == "user:u2"


def test_publish_without_draft_raises(service) -> None:
    with pytest.raises(NotFoundError):
        service.publish("agent-a")


def test_publish_rejects_duplicate_capability(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")
    service.save_draft("agent-b", DEFINITION_V1, "user:u1")

    with pytest.raises(ConflictError, match="capability"):
        service.publish("agent-b")

    # Archiving the owner frees the capability.
    service.archive_all("agent-a")
    published = service.publish("agent-b")
    assert published.status == "published"


def test_same_capability_same_agent_republishes(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")
    service.save_draft("agent-a", DEFINITION_V2, "user:u1")

    republished = service.publish("agent-a")

    assert republished.version == 2
    assert service.get_published_definition("agent-a") == DEFINITION_V2
    versions = {e.version: e.status for e in service.list_versions("agent-a")}
    assert versions == {1: "archived", 2: "published"}


def test_rollback_restores_old_definition(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")
    service.save_draft("agent-a", DEFINITION_V2, "user:u1")
    service.publish("agent-a")

    rolled = service.rollback("agent-a", 1, "user:ops")

    assert rolled.version == 3
    assert rolled.status == "published"
    assert service.get_published_definition("agent-a") == DEFINITION_V1


def test_rollback_unknown_version_raises(service) -> None:
    with pytest.raises(NotFoundError):
        service.rollback("agent-a", 99, "user:ops")


def test_rollback_rejects_duplicate_capability(service) -> None:
    """rollback 与 publish 走同一 capability 冲突检查（防御层）。"""
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")
    other = AgentDefinition(capability="other_cap", runtime="velites", skill="q/other")
    service.save_draft("agent-a", other, "user:u1")
    service.publish("agent-a")
    # agent-b now owns DEFINITION_V1's capability; rolling agent-a back to v1
    # would collide with it.
    service.save_draft("agent-b", DEFINITION_V1, "user:u1")
    service.publish("agent-b")

    with pytest.raises(ConflictError, match="capability"):
        service.rollback("agent-a", 1, "user:ops")

    # Archiving the owner frees the capability; the rollback then lands.
    service.archive_all("agent-b")
    rolled = service.rollback("agent-a", 1, "user:ops")
    assert rolled.status == "published"
    assert service.get_published_definition("agent-a") == DEFINITION_V1


def test_db_index_rejects_second_published_capability(service, job_db, workspace_id) -> None:
    """DB 层真实 guard：绕过 service 直接写同 workspace 第二行同 capability published 必失败。"""
    from psycopg import IntegrityError

    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")

    with pytest.raises(IntegrityError), job_db.connect() as conn:
        conn.execute(
            "insert into versioned_entities("
            "id, entity_type, workspace_id, entity_key, version, status,"
            " definition_json, definition_hash, created_by)"
            " values ('agent:agent-b:v1', 'agent', %s, 'agent-b', 1, 'published',"
            " %s, 'hash-b', 'user:test')",
            (workspace_id, json.dumps(DEFINITION_V1.model_dump(mode="json"))),
        )

    # 另一个 workspace 的同 capability published 不撞索引（per-workspace 唯一）。
    other_ws = job_db.create_workspace("Other WS")["id"]
    with job_db.connect() as conn:
        conn.execute(
            "insert into versioned_entities("
            "id, entity_type, workspace_id, entity_key, version, status,"
            " definition_json, definition_hash, created_by)"
            " values ('agent:other:agent-b:v1', 'agent', %s, 'agent-b', 1, 'published',"
            " %s, 'hash-b', 'user:test')",
            (other_ws, json.dumps(DEFINITION_V1.model_dump(mode="json"))),
        )

    # 同一 agent re-publish（先归档旧版再发新版）不撞索引。
    service.save_draft("agent-a", DEFINITION_V2, "user:u1")
    republished = service.publish("agent-a")
    assert republished.version == 2


def test_archive_all_unpublishes(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")

    assert service.archive_all("agent-a") == 1
    assert service.get_published_definition("agent-a") is None
    assert service.archive_all("agent-a") == 0


def test_copy_creates_independent_draft(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")

    copied = service.copy("agent-a", "agent-b", "user:u2")

    assert copied.entity_key == "agent-b"
    assert copied.version == 1
    assert copied.status == "draft"
    assert AgentDefinition.model_validate(copied.definition) == DEFINITION_V1
    # The copy must not trip the capability guard while it stays a draft.
    with pytest.raises(ConflictError, match="capability"):
        service.publish("agent-b")


def test_copy_missing_source_raises(service) -> None:
    with pytest.raises(NotFoundError):
        service.copy("agent-missing", "agent-b", "user:u1")


def test_copy_rejects_empty_new_id(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    with pytest.raises(InvalidOperationError):
        service.copy("agent-a", "", "user:u1")


def test_copy_rejects_new_id_outside_charset(service) -> None:
    """#1173 codex 二轮（copy 路径，唯一不经 save_draft 的写面）：新键
    无条件过字符域——copy 直插 v1、永远是新建实体，不存在 grandfather 面。"""
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    with pytest.raises(InvalidOperationError, match="不在合法字符域"):
        service.copy("agent-a", "code:y", "user:u1")
    assert all(e.entity_key != "code:y" for e in service.list_latest())


def test_list_latest_and_published_definitions(service) -> None:
    service.save_draft("agent-a", DEFINITION_V1, "user:u1")
    service.publish("agent-a")
    service.save_draft("agent-a", DEFINITION_V2, "user:u1")
    other = AgentDefinition(capability="generate_key_info", runtime="velites", skill="q/gen")
    service.save_draft("agent-b", other, "user:u1")
    service.publish("agent-b")

    latest = {e.entity_key: e for e in service.list_latest()}
    assert latest["agent-a"].status == "draft"
    assert latest["agent-b"].status == "published"

    published = {d.capability for d in service.list_published_definitions()}
    assert published == {"review_keywords", "generate_key_info"}
