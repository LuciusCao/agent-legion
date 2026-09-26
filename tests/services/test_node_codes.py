"""Custom node code service: validation, draft/publish flow, rollback, gate."""

from __future__ import annotations

import pytest

from server.app.services.job_errors import (
    ConflictError,
    CustomNodesDisabledError,
    InvalidOperationError,
    NotFoundError,
)
from server.app.services.node_code_pins import frozen_dispatch_pin
from server.app.services.node_code_resolution import (
    freeze_node_code_versions,
    resolve_dispatch_node_code,
)
from server.app.services.node_codes import (
    DEFAULT_MAX_CODE_BYTES,
    NodeCodeService,
    code_hash,
    validate_node_code,
)

VALID_CODE = "def run(job, job_dir, runtime):\n    return None\n"
UPDATED_CODE = "async def run(job, job_dir, runtime):\n    return 1\n"
WF = "demo_workflow"
NODE = "fetch_items"


@pytest.fixture
def service(job_db):
    return NodeCodeService(job_db.dsn_identity)


@pytest.fixture
def workspace_id(job_db):
    return job_db.create_workspace(default_workflow_key="demo_workflow", name="node-codes")["id"]


def test_save_draft_creates_version_one_with_hash(service, workspace_id) -> None:
    row = service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1", "first pass")

    assert row["version"] == 1
    assert row["status"] == "draft"
    assert row["created_by"] == "user:u1"
    assert row["change_note"] == "first pass"
    assert len(row["code_hash"]) == 64
    # A draft is not the effective code yet: the node stays builtin.
    assert service.get_effective_code(workspace_id, WF, NODE) is None


def test_save_draft_rejects_invalid_code(service, workspace_id) -> None:
    with pytest.raises(InvalidOperationError, match="not valid Python"):
        service.save_draft(workspace_id, WF, NODE, "def run(:\n", "user:u1")
    with pytest.raises(InvalidOperationError, match="module-level 'run'"):
        service.save_draft(workspace_id, WF, NODE, "X = 1\n", "user:u1")
    oversized = VALID_CODE + "#" * DEFAULT_MAX_CODE_BYTES
    with pytest.raises(InvalidOperationError, match="size limit"):
        service.save_draft(workspace_id, WF, NODE, oversized, "user:u1")
    assert service.list_versions(workspace_id, WF, NODE) == []


@pytest.mark.no_db
def test_validate_node_code_default_64kb_boundary() -> None:
    """#628: the default budget is unchanged 64KB — at-limit passes, +1 byte
    rejects, and the error names the configured limit."""
    pad = "#" * (DEFAULT_MAX_CODE_BYTES - len(VALID_CODE.encode("utf-8")))
    validate_node_code(VALID_CODE + pad)
    with pytest.raises(InvalidOperationError, match=r"65536-byte size limit"):
        validate_node_code(VALID_CODE + pad + "#")


@pytest.mark.no_db
def test_validate_node_code_custom_limit_applies() -> None:
    """#628: an injected larger budget admits code the 64KB default rejects."""
    limit = 128 * 1024
    pad = "#" * (limit - len(VALID_CODE.encode("utf-8")))
    oversized_for_default = VALID_CODE + pad
    with pytest.raises(InvalidOperationError):
        validate_node_code(oversized_for_default)
    validate_node_code(oversized_for_default, limit)
    with pytest.raises(InvalidOperationError, match=r"131072-byte"):
        validate_node_code(oversized_for_default + "#", limit)


def test_save_draft_custom_limit_via_service(job_db, workspace_id) -> None:
    """#628: NodeCodeService carries the settings-injected limit through both
    validating write paths (save_draft and seed_global)."""
    limit = 2048
    service = NodeCodeService(job_db.dsn_identity, max_code_bytes=limit)
    pad = "#" * (limit - len(VALID_CODE.encode("utf-8")))
    row = service.save_draft(workspace_id, WF, NODE, VALID_CODE + pad, "user:u1")
    assert row["status"] == "draft"
    with pytest.raises(InvalidOperationError, match="2048-byte"):
        service.save_draft(workspace_id, WF, NODE, VALID_CODE + pad + "#", "user:u1")
    with pytest.raises(InvalidOperationError, match="2048-byte"):
        service.seed_global(WF, "seeded", VALID_CODE + pad + "#", "seed too big")


def _padded_code(pad_to: int) -> str:
    return VALID_CODE + "#" * (pad_to - len(VALID_CODE.encode("utf-8")))


def test_publish_rejects_draft_over_lowered_limit(job_db, workspace_id) -> None:
    """#628 review P2: node_code_max_bytes is restart-effective. A draft saved
    under a higher budget must not survive a publish after the instance
    lowered the limit — and the rejection leaves the previous published
    version effective and the draft intact (nothing was archived)."""
    big = _padded_code(4096)
    roomy = NodeCodeService(job_db.dsn_identity, max_code_bytes=4096)
    roomy.save_draft(workspace_id, WF, NODE, big, "user:u1")
    roomy.publish(workspace_id, WF, NODE)

    roomy.save_draft(workspace_id, WF, NODE, _padded_code(2048), "user:u1", "smaller")
    lowered = NodeCodeService(job_db.dsn_identity, max_code_bytes=1024)
    with pytest.raises(
        InvalidOperationError,
        match=r"cannot publish node code of \d+ bytes: it exceeds the current 1024-byte",
    ) as exc_info:
        lowered.publish(workspace_id, WF, NODE)
    assert "lowered after this version was saved" in str(exc_info.value)
    # The previous published version stays effective; the rejected draft survives.
    assert lowered.get_effective_code(workspace_id, WF, NODE)["code"] == big
    versions = {
        row["version"]: row["status"] for row in lowered.list_versions(workspace_id, WF, NODE)
    }
    assert versions == {1: "published", 2: "draft"}


def test_publish_binds_validated_bytes_via_hash_cas(job_db, workspace_id, monkeypatch) -> None:
    """#628 review P2 + #692：校验读与发布之间草稿被并发覆盖（换成超限内
    容）时，发布绑定的是「校验过的那份字节」——expected_hash CAS 让本次
    发布以 Conflict 失败，超限字节绝不进 published。注入点：
    ``_check_publish_size`` 恰在预读之后、store 发布之前。突变自检：若
    publish 不绑定校验读到的 hash（传 None），覆盖后的内容被静默发布，
    本测试即红。"""
    lowered = NodeCodeService(job_db.dsn_identity, max_code_bytes=1024)
    lowered.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    roomy = NodeCodeService(job_db.dsn_identity, max_code_bytes=4096)

    def overwrite_after_validation(code: str, action: str) -> None:
        # 预读返回的是 VALID_CODE 草稿（对 1024 上限合法）；此刻另一会话
        # 把草稿覆盖为超限内容——本实例校验的是覆盖前读到的字节，放行。
        roomy.save_draft(workspace_id, WF, NODE, _padded_code(2048), "user:u2")

    monkeypatch.setattr(lowered, "_check_publish_size", overwrite_after_validation)

    with pytest.raises(ConflictError, match="draft hash mismatch"):
        lowered.publish(workspace_id, WF, NODE)

    # 零发布副作用：无 published 行；超限内容仍是草稿，等待人工处置。
    assert lowered.get_effective_code(workspace_id, WF, NODE) is None
    versions = {
        row["version"]: row["status"] for row in lowered.list_versions(workspace_id, WF, NODE)
    }
    assert versions == {1: "draft"}


def test_rollback_rejects_old_version_over_lowered_limit(job_db, workspace_id) -> None:
    """#628 review P2: rollback re-publishes a historical version as a NEW
    publish — the bytes must clear the CURRENT limit too, or the rejection
    leaves the currently published version untouched."""
    roomy = NodeCodeService(job_db.dsn_identity, max_code_bytes=4096)
    roomy.save_draft(workspace_id, WF, NODE, _padded_code(4096), "user:u1", "big")
    roomy.publish(workspace_id, WF, NODE)
    roomy.save_draft(workspace_id, WF, NODE, _padded_code(1024), "user:u1", "small")
    roomy.publish(workspace_id, WF, NODE)

    lowered = NodeCodeService(job_db.dsn_identity, max_code_bytes=2048)
    with pytest.raises(InvalidOperationError, match="cannot rollback node code"):
        lowered.rollback(workspace_id, WF, NODE, 1, "user:ops")
    versions = {
        row["version"]: row["status"] for row in lowered.list_versions(workspace_id, WF, NODE)
    }
    # v2 stays published; no v3 was created.
    assert versions == {1: "archived", 2: "published"}
    assert lowered.get_effective_code(workspace_id, WF, NODE)["code"] == _padded_code(1024)


def test_publish_and_rollback_within_limit_still_succeed(job_db, workspace_id) -> None:
    """#628 review P2: code under the CURRENT limit publishes and rolls back
    unchanged — the guard adds no false rejections. The exact-at-limit draft
    (2048 bytes under a 2048 limit) exercises the boundary."""
    service = NodeCodeService(job_db.dsn_identity, max_code_bytes=2048)
    at_limit = _padded_code(2048)
    service.save_draft(workspace_id, WF, NODE, at_limit, "user:u1")
    published = service.publish(workspace_id, WF, NODE)
    assert published["status"] == "published"

    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1", "smaller")
    published = service.publish(workspace_id, WF, NODE)
    assert published["version"] == 2
    rolled = service.rollback(workspace_id, WF, NODE, 1, "user:ops")
    assert rolled["version"] == 3
    assert rolled["status"] == "published"
    assert rolled["code"] == at_limit
    assert service.get_effective_code(workspace_id, WF, NODE)["code"] == at_limit


def test_save_draft_overwrites_existing_draft(service, workspace_id) -> None:
    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    row = service.save_draft(workspace_id, WF, NODE, UPDATED_CODE, "user:u2")

    assert row["version"] == 1
    assert row["code"] == UPDATED_CODE
    assert row["created_by"] == "user:u2"
    assert len(service.list_versions(workspace_id, WF, NODE)) == 1


def test_publish_flow_archives_previous_published(service, workspace_id) -> None:
    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    published = service.publish(workspace_id, WF, NODE)

    assert published["status"] == "published"
    assert published["published_at"] is not None
    assert service.get_effective_code(workspace_id, WF, NODE)["code"] == VALID_CODE

    service.save_draft(workspace_id, WF, NODE, UPDATED_CODE, "user:u1")
    republished = service.publish(workspace_id, WF, NODE)

    assert republished["version"] == 2
    versions = {
        row["version"]: row["status"] for row in service.list_versions(workspace_id, WF, NODE)
    }
    assert versions == {1: "archived", 2: "published"}
    assert service.get_effective_code(workspace_id, WF, NODE)["code"] == UPDATED_CODE


def test_publish_without_draft_raises(service, workspace_id) -> None:
    with pytest.raises(NotFoundError):
        service.publish(workspace_id, WF, NODE)


def test_rollback_republishes_old_version_as_new(service, workspace_id) -> None:
    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    service.publish(workspace_id, WF, NODE)
    service.save_draft(workspace_id, WF, NODE, UPDATED_CODE, "user:u1")
    service.publish(workspace_id, WF, NODE)

    rolled = service.rollback(workspace_id, WF, NODE, 1, "user:ops")

    assert rolled["version"] == 3
    assert rolled["status"] == "published"
    assert rolled["code"] == VALID_CODE
    assert rolled["change_note"] == "rollback to v1"
    versions = {
        row["version"]: row["status"] for row in service.list_versions(workspace_id, WF, NODE)
    }
    assert versions == {1: "archived", 2: "archived", 3: "published"}
    # The source version stays immutable.
    assert service.list_versions(workspace_id, WF, NODE)[-1]["code"] == VALID_CODE


def test_rollback_unknown_version_raises(service, workspace_id) -> None:
    with pytest.raises(NotFoundError):
        service.rollback(workspace_id, WF, NODE, 99, "user:ops")


def test_archive_all_falls_back_to_builtin(service, workspace_id) -> None:
    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    service.publish(workspace_id, WF, NODE)

    archived = service.archive_all(workspace_id, WF, NODE)

    assert archived == 1
    assert service.get_effective_code(workspace_id, WF, NODE) is None
    assert service.list_versions(workspace_id, WF, NODE)[0]["status"] == "archived"
    # Idempotent: nothing left to archive.
    assert service.archive_all(workspace_id, WF, NODE) == 0


def test_versions_number_by_max_plus_one(service, workspace_id) -> None:
    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    service.archive_all(workspace_id, WF, NODE)
    row = service.save_draft(workspace_id, WF, NODE, UPDATED_CODE, "user:u1")

    assert row["version"] == 2


def test_gate_disabled_rejects_every_entry(job_db, workspace_id) -> None:
    gated = NodeCodeService(job_db.dsn_identity, custom_nodes_enabled=False)

    with pytest.raises(CustomNodesDisabledError):
        gated.get_effective_code(workspace_id, WF, NODE)
    with pytest.raises(CustomNodesDisabledError):
        gated.list_versions(workspace_id, WF, NODE)
    with pytest.raises(CustomNodesDisabledError):
        gated.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    with pytest.raises(CustomNodesDisabledError):
        gated.publish(workspace_id, WF, NODE)
    with pytest.raises(CustomNodesDisabledError):
        gated.rollback(workspace_id, WF, NODE, 1, "user:u1")
    with pytest.raises(CustomNodesDisabledError):
        gated.archive_all(workspace_id, WF, NODE)


def test_get_code_by_version_reads_archived_rows(service, workspace_id) -> None:
    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    service.publish(workspace_id, WF, NODE)
    service.save_draft(workspace_id, WF, NODE, UPDATED_CODE, "user:u1")
    service.publish(workspace_id, WF, NODE)

    row = service.get_code_by_version(workspace_id, WF, NODE, 1)

    assert row is not None
    assert row["status"] == "archived"
    assert row["code"] == VALID_CODE
    assert service.get_code_by_version(workspace_id, WF, NODE, 99) is None


def test_freeze_node_code_versions_pins_only_published(job_db, service, workspace_id) -> None:
    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    service.publish(workspace_id, WF, NODE)
    # A draft without publish is not pinned.
    service.save_draft(workspace_id, WF, NODE, UPDATED_CODE, "user:u1")

    pins = freeze_node_code_versions(
        job_db.dsn_identity, True, workspace_id, WF, [NODE, "fetch_media"]
    )

    assert list(pins) == [NODE]
    assert pins[NODE]["version"] == 1
    published = service.get_code_by_version(workspace_id, WF, NODE, 1)
    assert pins[NODE]["code_hash"] == published["code_hash"]
    # Gate off: intake never touches the table.
    assert freeze_node_code_versions(job_db.dsn_identity, False, workspace_id, WF, [NODE]) == {}


def test_resolve_dispatch_node_code_priority(job_db, service, workspace_id) -> None:
    # Builtin: no custom code at all.
    assert (
        resolve_dispatch_node_code(job_db.dsn_identity, True, workspace_id, WF, NODE, None) is None
    )
    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    service.publish(workspace_id, WF, NODE)
    assert (
        resolve_dispatch_node_code(job_db.dsn_identity, True, workspace_id, WF, NODE, None)
        == VALID_CODE
    )
    # A frozen job keeps v1 even after v2 is published.
    service.save_draft(workspace_id, WF, NODE, UPDATED_CODE, "user:u1")
    service.publish(workspace_id, WF, NODE)
    frozen = {"version": 1, "code_hash": code_hash(VALID_CODE)}
    assert (
        resolve_dispatch_node_code(job_db.dsn_identity, True, workspace_id, WF, NODE, frozen)
        == VALID_CODE
    )
    # Archived frozen versions stay readable.
    service.archive_all(workspace_id, WF, NODE)
    assert (
        resolve_dispatch_node_code(job_db.dsn_identity, True, workspace_id, WF, NODE, frozen)
        == VALID_CODE
    )
    assert (
        resolve_dispatch_node_code(job_db.dsn_identity, True, workspace_id, WF, NODE, None) is None
    )
    # Gate off: builtin, no error.
    assert (
        resolve_dispatch_node_code(job_db.dsn_identity, False, workspace_id, WF, NODE, frozen)
        is None
    )


def test_resolve_dispatch_node_code_rejects_hash_mismatch(job_db, service, workspace_id) -> None:
    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    service.publish(workspace_id, WF, NODE)

    frozen = {"version": 1, "code_hash": "tampered"}
    with pytest.raises(ValueError, match="hash mismatch"):
        resolve_dispatch_node_code(job_db.dsn_identity, True, workspace_id, WF, NODE, frozen)


def test_resolve_dispatch_node_code_fails_closed_on_missing_version(
    job_db, service, workspace_id
) -> None:
    """A frozen version missing at BOTH scopes is data corruption: fail
    closed instead of silently running the current published code."""
    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    service.publish(workspace_id, WF, NODE)

    frozen = {"version": 99, "code_hash": "whatever"}
    with pytest.raises(ValueError, match="frozen node code version missing"):
        resolve_dispatch_node_code(job_db.dsn_identity, True, workspace_id, WF, NODE, frozen)


@pytest.mark.no_db
def test_frozen_dispatch_pin_prefers_snapshot_pins() -> None:
    """#109: the job snapshot's node_code_pins win over the batch payload's
    node_code_versions (upgrade refreshes only the former) — inside a
    quality-replay batch, the only place pins still apply (#115)."""
    snapshot_pins = {"n": {"version": 2, "code_hash": "h2"}}
    batch_payload = {
        "quality_replay": {"replay_id": "r1"},
        "node_code_versions": {"n": {"version": 1, "code_hash": "h1"}},
    }

    assert frozen_dispatch_pin(snapshot_pins, batch_payload, "n") == {
        "version": 2,
        "code_hash": "h2",
    }


def test_save_draft_guard_rejects_concurrently_published_row(
    service, workspace_id, monkeypatch
) -> None:
    """A stale draft view must not overwrite a row published in between."""
    import server.app.services.versioned_entities as versioned_entities

    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    published = service.publish(workspace_id, WF, NODE)
    monkeypatch.setattr(versioned_entities, "_latest_with_status", lambda *args: dict(published))
    with pytest.raises(ConflictError):
        service.save_draft(workspace_id, WF, NODE, UPDATED_CODE, "user:u2")
    # The published row is untouched.
    assert service.get_effective_code(workspace_id, WF, NODE)["code"] == VALID_CODE


def test_publish_guard_rejects_concurrently_archived_draft(service, workspace_id) -> None:
    """A stale draft view must not resurrect an archived row into published.

    #628 review P2（CAS 复检设计）后服务侧 publish 先预读当前草稿：草稿
    已被并发归档时预读即找不到草稿，NotFound（404）先行；残余窗口（预读
    之后才归档）由 store 的 status 谓词 CAS 兜底为 Conflict——store 层
    语义由 tests/services/test_versioned_entities.py 的同名钉保持。
    """
    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    service.archive_all(workspace_id, WF, NODE)
    with pytest.raises(NotFoundError):
        service.publish(workspace_id, WF, NODE)
    assert service.get_effective_code(workspace_id, WF, NODE) is None


def test_insert_version_collision_maps_to_conflict_error(
    service, workspace_id, monkeypatch
) -> None:
    """A unique-constraint race surfaces as ConflictError (409), not a 500."""
    import server.app.services.versioned_entities as versioned_entities

    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    service.archive_all(workspace_id, WF, NODE)
    monkeypatch.setattr(versioned_entities, "_next_version", lambda *args: 1)
    with pytest.raises(ConflictError):
        service.save_draft(workspace_id, WF, NODE, UPDATED_CODE, "user:u1")


GLOBAL_CODE = "def run(job, job_dir, runtime):\n    return 'global'\n"


def test_frozen_pin_matches_across_scopes_by_hash(job_db, service, workspace_id) -> None:
    """Pin scope collision (review P1-2): the job froze the global seed v1 at
    intake; a later workspace publish also numbered v1. The pin's code_hash —
    not the scope — identifies the frozen code, so the old job must still
    resolve the global code instead of erroring on the workspace row."""
    assert service.seed_global(WF, NODE, GLOBAL_CODE, "test seed")
    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    service.publish(workspace_id, WF, NODE)

    frozen = {"version": 1, "code_hash": code_hash(GLOBAL_CODE)}
    resolved = resolve_dispatch_node_code(job_db.dsn_identity, True, workspace_id, WF, NODE, frozen)
    assert resolved == GLOBAL_CODE

    # And the workspace pin still resolves the workspace code.
    frozen_ws = {"version": 1, "code_hash": code_hash(VALID_CODE)}
    assert (
        resolve_dispatch_node_code(job_db.dsn_identity, True, workspace_id, WF, NODE, frozen_ws)
        == VALID_CODE
    )


def test_frozen_pin_matching_neither_scope_still_fails_closed(
    job_db, service, workspace_id
) -> None:
    assert service.seed_global(WF, NODE, GLOBAL_CODE, "test seed")
    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    service.publish(workspace_id, WF, NODE)

    frozen = {"version": 1, "code_hash": "tampered"}
    with pytest.raises(ValueError, match="hash mismatch"):
        resolve_dispatch_node_code(job_db.dsn_identity, True, workspace_id, WF, NODE, frozen)


def test_seed_global_tolerates_concurrent_seed_race(service, monkeypatch) -> None:
    """Two Host processes starting together both pass the emptiness check;
    the loser's insert hits the version-allocation unique index
    (ConflictError). Treat it as already seeded instead of crashing startup.

    Honest scope: "already seeded" is not a guarantee the winner's row
    stays published. If the loser's save_draft lands only AFTER the
    winner's draft+publish committed, it allocates v2, publishes it, and
    archives the winner's v1 — the loser overwrites the winner. This is
    accepted: concurrent seeds carry identical factory content (same
    source file), so the published code is the same either way, and the
    window exists only on first startup of an un-seeded database."""
    import server.app.services.versioned_entities as versioned_entities

    assert service.seed_global(WF, NODE, GLOBAL_CODE, "test seed")
    # Stale view: the loser still sees an empty entity and re-attempts v1.
    monkeypatch.setattr(service._store, "list_versions", lambda *args, **kwargs: [])
    monkeypatch.setattr(versioned_entities, "_next_version", lambda *args: 1)

    other = "def run(job, job_dir, runtime):\n    return 'other'\n"
    assert not service.seed_global(WF, NODE, other, "concurrent seed")
    assert service.get_global_published(WF, NODE)["code"] == GLOBAL_CODE


@pytest.mark.no_db
def test_frozen_dispatch_pin_falls_back_to_batch_payload() -> None:
    """Legacy rows (no snapshot pins) keep resolving the intake batch pin —
    again only within a quality-replay batch (#115)."""
    batch_payload = {
        "quality_replay": {"replay_id": "r1"},
        "node_code_versions": {"n": {"version": 1, "code_hash": "h1"}},
    }
    expected = {"version": 1, "code_hash": "h1"}

    assert frozen_dispatch_pin(None, batch_payload, "n") == expected
    assert frozen_dispatch_pin({}, batch_payload, "n") == expected
    assert frozen_dispatch_pin({"other": {"version": 9}}, batch_payload, "n") == expected
    assert frozen_dispatch_pin({"n": None}, None, "n") is None
    assert frozen_dispatch_pin(None, None, "n") is None


@pytest.mark.no_db
def test_frozen_dispatch_pin_ignored_for_ordinary_jobs() -> None:
    """#115: ordinary jobs never pin — dispatch resolves the latest published
    code; the intake/snapshot pins survive as audit records and the replay
    pin source only."""
    snapshot_pins = {"n": {"version": 2, "code_hash": "h2"}}
    batch_payload = {"node_code_versions": {"n": {"version": 1, "code_hash": "h1"}}}

    assert frozen_dispatch_pin(snapshot_pins, batch_payload, "n") is None
    assert frozen_dispatch_pin(None, batch_payload, "n") is None
    assert frozen_dispatch_pin(snapshot_pins, {}, "n") is None
