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
    MAX_CODE_BYTES,
    NodeCodeService,
    code_hash,
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
    oversized = VALID_CODE + "#" * MAX_CODE_BYTES
    with pytest.raises(InvalidOperationError, match="size limit"):
        service.save_draft(workspace_id, WF, NODE, oversized, "user:u1")
    assert service.list_versions(workspace_id, WF, NODE) == []


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


def test_publish_guard_rejects_concurrently_archived_draft(
    service, workspace_id, monkeypatch
) -> None:
    """A stale draft view must not resurrect an archived row into published."""
    import server.app.services.versioned_entities as versioned_entities

    service.save_draft(workspace_id, WF, NODE, VALID_CODE, "user:u1")
    stale_draft = service.list_versions(workspace_id, WF, NODE)[0]
    service.archive_all(workspace_id, WF, NODE)
    monkeypatch.setattr(versioned_entities, "_latest_with_status", lambda *args: dict(stale_draft))
    with pytest.raises(ConflictError):
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
