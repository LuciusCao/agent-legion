from __future__ import annotations

import json
from typing import Any

import pytest

from server.app.db.migrations import job_node_needs_capacity as migration
from server.app.jobs import JobQueries

pytestmark = pytest.mark.postgres


def _payload(workspace_id: str, gate_type: str, *, field: str = "type") -> str:
    gate: dict[str, Any] = {field: gate_type, "label": "gate"}
    if gate_type != "approval":
        gate["capability"] = "gate"
    return json.dumps(
        {
            "key": workspace_id,
            "label": workspace_id,
            "schema_version": 2,
            "nodes": {
                "run": {"type": "code", "capability": "run"},
                "gate": gate,
            },
            "edges": [{"from": "run", "to": "gate"}],
        }
    )


def _publish_active(
    job_db: JobQueries, workspace_id: str, gate_type: str, *, version: int = 1
) -> None:
    raw = _payload(workspace_id, gate_type)
    job_db.create_workflow_revision(
        revision_id=f"rev-{workspace_id}-{gate_type}-{version}",
        workspace_id=workspace_id,
        workflow_key=workspace_id,
        version=version,
        status="active",
        definition_json=raw,
        definition_hash=f"active-{workspace_id}-{gate_type}",
    )


def _legacy_job(
    job_db: JobQueries,
    workspace_id: str,
    source_id: str,
    *,
    snapshot: str,
    definition_hash: str,
    source_type: str = "question",
) -> str:
    job = job_db.create_job(
        workflow_key=workspace_id,
        source_type=source_type,
        source_id=source_id,
        run_id="",
        title=source_id,
        node_keys=["run", "gate"],
        workspace_id=workspace_id,
        workflow_definition_hash=definition_hash,
        workflow_definition_snapshot_json=snapshot,
    )
    return str(job["id"])


def _needs(job_db: JobQueries, job_id: str) -> dict[str, bool]:
    return {
        str(row["node_key"]): bool(row["needs_capacity"]) for row in job_db.list_job_nodes(job_id)
    }


def _counter_rows(job_db: JobQueries) -> list[tuple[Any, ...]]:
    with job_db.connect() as conn:
        base = conn.execute(
            "select workspace_id, node_key, status, cnt from workspace_job_node_status_counts"
            " order by 1, 2, 3"
        ).fetchall()
        deltas = conn.execute(
            "select workspace_id, node_key, status, delta, id"
            " from workspace_job_node_status_count_deltas order by id"
        ).fetchall()
    return [tuple(row) for row in (*base, *deltas)]


def test_migration_legacy_matrix_idempotency_and_counter_parity(
    job_db: JobQueries, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(migration, "_BATCH_JOBS", 2)
    job_db.create_workspace("Legacy", workspace_id="legacy")
    _publish_active(job_db, "legacy", "code")
    own_approval = _payload("legacy", "approval", field="node_type")
    archived = _legacy_job(
        job_db,
        "legacy",
        "archived",
        snapshot=own_approval,
        definition_hash="archived-approval",
    )
    replay = _legacy_job(
        job_db,
        "legacy",
        "replay",
        snapshot=own_approval,
        definition_hash="quality-own-approval",
        source_type="quality_replay",
    )
    pinned_code = _legacy_job(
        job_db,
        "legacy",
        "pinned-code",
        snapshot=_payload("legacy", "code"),
        definition_hash="pinned-code",
    )
    donor = _legacy_job(
        job_db,
        "legacy",
        "donor",
        snapshot=_payload("legacy", "approval"),
        definition_hash="shared-approval",
    )
    donorless = _legacy_job(
        job_db,
        "legacy",
        "donorless",
        snapshot="",
        definition_hash="shared-approval",
    )
    no_donor = _legacy_job(
        job_db,
        "legacy",
        "no-donor",
        snapshot="",
        definition_hash="missing-donor",
    )
    job_db.update_job_node(archived, "gate", status="completed")
    job_db.update_job_node(replay, "gate", status="failed")
    with job_db.connect() as conn:
        conn.execute(
            "insert into workflow_revisions("
            "id, workspace_id, version, status, definition_json, definition_hash)"
            " values ('archived-rev', 'legacy', 90, 'archived', %s, 'archived-approval')",
            (own_approval,),
        )

    _publish_active(job_db, "legacy", "approval", version=2)
    snapshotless = _legacy_job(job_db, "legacy", "snapshotless", snapshot="", definition_hash="")
    corrupt = _legacy_job(job_db, "legacy", "corrupt", snapshot="{", definition_hash="corrupt")
    _publish_active(job_db, "legacy", "code", version=3)
    before_counts = _counter_rows(job_db)

    with job_db.connect() as conn:
        migration.migrate_job_node_needs_capacity(conn)
    with job_db.connect() as conn:
        migration.migrate_job_node_needs_capacity(conn)

    for job_id in (archived, replay, donor, donorless):
        assert _needs(job_db, job_id)["gate"] is False
    for job_id in (pinned_code, no_donor):
        assert _needs(job_db, job_id)["gate"] is True
    assert _needs(job_db, snapshotless)["gate"] is True
    assert _needs(job_db, corrupt)["gate"] is True
    assert _needs(job_db, archived)["run"] is True
    assert _counter_rows(job_db) == before_counts
    with job_db.connect() as conn:
        assert (
            conn.execute(
                "select count(*) as n from jobs where execution_generation != 0"
            ).fetchone()["n"]
            == 0
        )


def test_empty_hash_and_corrupt_snapshot_fall_back_to_active(job_db: JobQueries) -> None:
    job_db.create_workspace("Fallback", workspace_id="fallback")
    _publish_active(job_db, "fallback", "approval")
    empty_hash = _legacy_job(
        job_db, "fallback", "empty-hash", snapshot=_payload("fallback", "code"), definition_hash=""
    )
    corrupt = _legacy_job(
        job_db, "fallback", "corrupt", snapshot="{", definition_hash="corrupt-active"
    )
    missing_donor = _legacy_job(
        job_db, "fallback", "missing", snapshot="", definition_hash="not-present"
    )
    pinned_code = _legacy_job(
        job_db,
        "fallback",
        "pinned-code",
        snapshot=_payload("fallback", "code"),
        definition_hash="pinned-code-active-approval",
    )

    with job_db.connect() as conn:
        migration.migrate_job_node_needs_capacity(conn)

    for job_id in (empty_hash, corrupt, missing_donor):
        assert _needs(job_db, job_id) == {"run": True, "gate": False}
    assert _needs(job_db, pinned_code) == {"run": True, "gate": True}


def test_unknown_node_and_unreadable_active_definition_stay_fail_closed(
    job_db: JobQueries,
) -> None:
    job_db.create_workspace("Unknown", workspace_id="unknown")
    job_id = _legacy_job(
        job_db,
        "unknown",
        "unknown",
        snapshot=_payload("unknown", "approval"),
        definition_hash="own",
    )
    with job_db.connect() as conn:
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, 'removed')", (job_id,))
        migration.migrate_job_node_needs_capacity(conn)

    assert _needs(job_db, job_id) == {"run": True, "gate": False, "removed": True}
