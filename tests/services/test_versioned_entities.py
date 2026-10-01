"""VersionedEntityStore: shared draft → published → archived lifecycle engine."""

from __future__ import annotations

import pytest

from server.app.services import versioned_entities as versioned_entities_module
from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.services.versioned_entities import VersionedEntityStore

DEFINITION_V1 = {"code": "print('v1')\n", "change_note": "first"}
DEFINITION_V2 = {"code": "print('v2')\n", "change_note": "second"}


@pytest.fixture
def store(job_db):
    return VersionedEntityStore(job_db.dsn_identity, "node_code")


@pytest.fixture
def workspace_id(job_db):
    return job_db.create_workspace(default_workflow_key="demo_workflow", name="ve-store")["id"]


def test_save_draft_creates_version_one(store, workspace_id) -> None:
    entity = store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")

    assert entity.version == 1
    assert entity.status == "draft"
    assert entity.entity_type == "node_code"
    assert entity.workspace_id == workspace_id
    assert entity.definition == DEFINITION_V1
    assert entity.definition_hash == "hash1"
    assert entity.created_by == "user:u1"
    assert store.get_published("wf:node", workspace_id) is None


def test_save_draft_overwrites_existing_draft(store, workspace_id) -> None:
    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    entity = store.save_draft("wf:node", DEFINITION_V2, "hash2", workspace_id, "user:u2")

    assert entity.version == 1
    assert entity.definition == DEFINITION_V2
    assert entity.created_by == "user:u2"
    assert len(store.list_versions("wf:node", workspace_id)) == 1


def test_publish_flow_archives_previous_published(store, workspace_id) -> None:
    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    published = store.publish("wf:node", workspace_id)

    assert published.status == "published"
    assert published.published_at is not None
    assert store.get_published("wf:node", workspace_id).definition == DEFINITION_V1

    store.save_draft("wf:node", DEFINITION_V2, "hash2", workspace_id, "user:u1")
    republished = store.publish("wf:node", workspace_id)

    assert republished.version == 2
    versions = {e.version: e.status for e in store.list_versions("wf:node", workspace_id)}
    assert versions == {1: "archived", 2: "published"}


def test_published_state_changes_take_implementation_revalidation_lock(
    store, workspace_id, monkeypatch
) -> None:
    """publish/rollback/archive 都必须加入升级重验的 workspace 锁域。"""
    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    lock_scopes: list[str | None] = []

    def observed_lock(conn, scope):
        lock_scopes.append(scope)

    monkeypatch.setattr(
        versioned_entities_module, "acquire_implementation_publication_lock", observed_lock
    )
    store.publish("wf:node", workspace_id)
    store.rollback("wf:node", 1, workspace_id, "user:ops")
    store.archive_all("wf:node", workspace_id)

    assert lock_scopes == [workspace_id, workspace_id, workspace_id]


def test_publish_without_draft_raises(store, workspace_id) -> None:
    with pytest.raises(NotFoundError):
        store.publish("wf:node", workspace_id)


# #692 codex P1（第三轮补充）：expected_hash 在选择 draft 的同一事务内
# 核对——不匹配 409 且零发布副作用（这是「读-比对-发布」TOCTOU 窗口的
# 原子收口，替代客户端预检式核对）。
def test_publish_expected_hash_mismatch_conflicts_with_zero_side_effects(
    store, workspace_id
) -> None:
    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    # 模拟其他会话在调用方预检后覆盖草稿（save_draft 原地 UPDATE，hash 变）。
    store.save_draft("wf:node", DEFINITION_V2, "hash2", workspace_id, "user:u1")

    with pytest.raises(ConflictError):
        store.publish("wf:node", workspace_id, expected_hash="hash1")

    # 零副作用：草稿仍是 hash2 的内容，无 published 行。
    statuses = {e.status for e in store.list_versions("wf:node", workspace_id)}
    assert statuses == {"draft"}
    assert store.get_published("wf:node", workspace_id) is None


# #692 R6 P1-1：SELECT-时核对在 READ COMMITTED 下不是原子的——并发
# save_draft 在本事务 SELECT 与 UPDATE 之间提交时，EvalPlanCheck 对最新
# 行版本重估 status 谓词，发布的是覆盖后的内容。CAS（hash 进 UPDATE 的
# WHERE）后 rowcount=0 → Conflict，事务回滚。
# 钉法（R7 P2-1）：monkeypatch 注入式——把 T2 的 save_draft 注入到生产
# publish 事务内部的 SELECT 与 UPDATE 之间（同文件 :274/:290 的
# _latest_status_row monkeypatch 先例），走完整生产路径。突变自证过：
# 删掉生产 CAS 谓词时本测试必红（回归到 R6 P1-1 的静默穿透形态）。
def test_publish_expected_hash_cas_blocks_read_committed_interleave(
    store, workspace_id, monkeypatch
) -> None:
    import server.app.services.versioned_entities as ve

    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")

    original = ve._latest_status_row
    fired: list[bool] = []

    def interleaved(conn, *args, **kwargs):
        row = original(conn, *args, **kwargs)
        # 只在 publish 的 draft 查找上注入一次：T1 事务内 SELECT 返回
        # hash1 快照之后、CAS UPDATE 之前，T2 覆盖并提交（hash2）。
        if row is not None and row["status"] == "draft" and not fired:
            fired.append(True)
            store.save_draft("wf:node", DEFINITION_V2, "hash2", workspace_id, "user:u1")
        return row

    monkeypatch.setattr(ve, "_latest_status_row", interleaved)

    with pytest.raises(ConflictError):
        store.publish("wf:node", workspace_id, expected_hash="hash1")

    # 交错确实发生（T2 在 T1 的 SELECT 后注入）。
    assert fired == [True]
    # 零副作用：CAS rowcount=0 回滚了 archive 语句——草稿仍是 hash2 的
    # 内容，无 published 行（穿透形态则会是 published + hash2）。
    statuses = {e.status for e in store.list_versions("wf:node", workspace_id)}
    assert statuses == {"draft"}
    assert store.get_published("wf:node", workspace_id) is None


def test_publish_expected_hash_match_publishes(store, workspace_id) -> None:
    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")

    published = store.publish("wf:node", workspace_id, expected_hash="hash1")

    assert published.status == "published"
    assert store.get_published("wf:node", workspace_id).definition == DEFINITION_V1


def test_rollback_republishes_old_version_as_new(store, workspace_id) -> None:
    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    store.publish("wf:node", workspace_id)
    store.save_draft("wf:node", DEFINITION_V2, "hash2", workspace_id, "user:u1")
    store.publish("wf:node", workspace_id)

    rolled = store.rollback(
        "wf:node", 1, workspace_id, "user:ops", definition_patch={"change_note": "rollback to v1"}
    )

    assert rolled.version == 3
    assert rolled.status == "published"
    assert rolled.definition == {"code": DEFINITION_V1["code"], "change_note": "rollback to v1"}
    assert rolled.definition_hash == "hash1"
    versions = {e.version: e.status for e in store.list_versions("wf:node", workspace_id)}
    assert versions == {1: "archived", 2: "archived", 3: "published"}
    # The source version stays immutable.
    assert store.get_version("wf:node", 1, workspace_id).definition == DEFINITION_V1


def test_rollback_unknown_version_raises(store, workspace_id) -> None:
    with pytest.raises(NotFoundError):
        store.rollback("wf:node", 99, workspace_id, "user:ops")


def test_archive_all_is_idempotent(store, workspace_id) -> None:
    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    store.publish("wf:node", workspace_id)

    assert store.archive_all("wf:node", workspace_id) == 1
    assert store.get_published("wf:node", workspace_id) is None
    assert store.archive_all("wf:node", workspace_id) == 0


def test_versions_number_by_max_plus_one(store, workspace_id) -> None:
    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    store.archive_all("wf:node", workspace_id)
    entity = store.save_draft("wf:node", DEFINITION_V2, "hash2", workspace_id, "user:u1")

    assert entity.version == 2


def test_copy_duplicates_latest_definition_as_draft(store, workspace_id) -> None:
    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    store.publish("wf:node", workspace_id)

    copied = store.copy("wf:node", "wf:node2", workspace_id, "user:u2")

    assert copied.entity_key == "wf:node2"
    assert copied.version == 1
    assert copied.status == "draft"
    assert copied.definition == DEFINITION_V1
    assert copied.definition_hash == "hash1"


def test_copy_missing_source_raises(store, workspace_id) -> None:
    with pytest.raises(NotFoundError):
        store.copy("wf:missing", "wf:new", workspace_id, "user:u1")


def test_copy_existing_key_conflicts(store, workspace_id) -> None:
    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    store.save_draft("wf:node2", DEFINITION_V2, "hash2", workspace_id, "user:u1")

    with pytest.raises(ConflictError):
        store.copy("wf:node", "wf:node2", workspace_id, "user:u1")


def test_list_latest_prefers_draft_over_published(store, workspace_id) -> None:
    store.save_draft("wf:a", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    store.publish("wf:a", workspace_id)
    store.save_draft("wf:a", DEFINITION_V2, "hash2", workspace_id, "user:u1")
    store.save_draft("wf:b", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    store.publish("wf:b", workspace_id)

    latest = {e.entity_key: e for e in store.list_latest(workspace_id)}

    assert latest["wf:a"].status == "draft"
    assert latest["wf:a"].definition == DEFINITION_V2
    assert latest["wf:b"].status == "published"


def test_list_published_and_keys(store, workspace_id) -> None:
    store.save_draft("wf:a", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    store.publish("wf:a", workspace_id)
    store.save_draft("wf:b", DEFINITION_V2, "hash2", workspace_id, "user:u1")

    published = store.list_published(workspace_id)
    assert [e.entity_key for e in published] == ["wf:a"]
    keyed = store.list_published_keys(workspace_id, ["wf:a", "wf:b"])
    assert [e.entity_key for e in keyed] == ["wf:a"]
    assert store.list_published_keys(workspace_id, []) == []


def test_workspace_scopes_are_isolated(job_db, store, workspace_id) -> None:
    other_workspace = job_db.create_workspace(
        default_workflow_key="demo_workflow", name="ve-store-other"
    )["id"]
    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    store.publish("wf:node", workspace_id)

    assert store.get_published("wf:node", other_workspace) is None
    assert store.list_versions("wf:node", other_workspace) == []
    # Global (NULL workspace) entities are a third, independent scope.
    assert store.get_published("wf:node", None) is None


def test_global_entities_publish_with_null_workspace(job_db) -> None:
    store = VersionedEntityStore(job_db.dsn_identity, "agent")
    store.save_draft("agent-1", {"capability": "cap"}, "hash1", None, "user:u1")
    published = store.publish("agent-1", None)

    assert published.workspace_id is None
    assert published.status == "published"
    assert store.get_published("agent-1", None).definition == {"capability": "cap"}


def test_get_version_reads_archived_rows(store, workspace_id) -> None:
    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    store.publish("wf:node", workspace_id)
    store.archive_all("wf:node", workspace_id)

    entity = store.get_version("wf:node", 1, workspace_id)

    assert entity is not None
    assert entity.status == "archived"
    assert store.get_version("wf:node", 99, workspace_id) is None


def test_save_draft_guard_rejects_concurrently_published_row(
    store, workspace_id, monkeypatch
) -> None:
    import server.app.services.versioned_entities as versioned_entities

    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    store.publish("wf:node", workspace_id)
    published = store.get_published("wf:node", workspace_id)
    # 只拦截 draft 读取（save_draft/publish 的写路径）；get_published 的
    # 断言读走原实现（get_published 与写路径共用 _latest_status_row 查询件）。
    original = versioned_entities._latest_status_row
    monkeypatch.setattr(
        versioned_entities,
        "_latest_status_row",
        lambda conn, et, ws, key, status: (
            {"id": published.id} if status == "draft" else original(conn, et, ws, key, status)
        ),
    )
    with pytest.raises(ConflictError):
        store.save_draft("wf:node", DEFINITION_V2, "hash2", workspace_id, "user:u2")
    assert store.get_published("wf:node", workspace_id).definition == DEFINITION_V1


def test_publish_guard_rejects_concurrently_archived_draft(
    store, workspace_id, monkeypatch
) -> None:
    import server.app.services.versioned_entities as versioned_entities

    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    draft_id = store.list_versions("wf:node", workspace_id)[0].id
    store.archive_all("wf:node", workspace_id)
    # 只拦截 draft 读取（publish 的写路径）；get_published 的断言读走原实现。
    original = versioned_entities._latest_status_row
    monkeypatch.setattr(
        versioned_entities,
        "_latest_status_row",
        lambda conn, et, ws, key, status: (
            {"id": draft_id} if status == "draft" else original(conn, et, ws, key, status)
        ),
    )
    with pytest.raises(ConflictError):
        store.publish("wf:node", workspace_id)
    assert store.get_published("wf:node", workspace_id) is None


def test_insert_version_collision_maps_to_conflict_error(store, workspace_id, monkeypatch) -> None:
    import server.app.services.versioned_entities as versioned_entities

    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    store.archive_all("wf:node", workspace_id)
    monkeypatch.setattr(versioned_entities, "_next_version", lambda *args: 1)
    with pytest.raises(ConflictError):
        store.save_draft("wf:node", DEFINITION_V2, "hash2", workspace_id, "user:u1")


def test_get_draft_returns_only_the_current_draft(store, workspace_id) -> None:
    """#779 列车 R2 复审 P2-B 的窄读取路径：只取当前 draft 行（不读全部
    永久版本历史）；无草稿/草稿已发布后返回 None。"""
    assert store.get_draft("wf:node", workspace_id) is None
    store.save_draft("wf:node", DEFINITION_V1, "hash1", workspace_id, "user:u1")
    draft = store.get_draft("wf:node", workspace_id)
    assert draft is not None
    assert draft.status == "draft"
    assert draft.definition == DEFINITION_V1

    store.publish("wf:node", workspace_id)
    assert store.get_draft("wf:node", workspace_id) is None

    store.save_draft("wf:node", DEFINITION_V2, "hash2", workspace_id, "user:u2")
    draft = store.get_draft("wf:node", workspace_id)
    assert draft is not None
    assert draft.version == 2
    assert draft.definition == DEFINITION_V2
