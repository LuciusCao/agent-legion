"""Demo node factory seed: global node_code versions for the demo workflow (#96).

conftest re-seeds the two global demo codes after every TRUNCATE (mirroring
app startup), so these tests exercise idempotency and the gate on top of
that baseline.
"""

from __future__ import annotations

from server.app.services.demo_node_seed import DEMO_WORKFLOW_KEY, seed_demo_node_codes
from server.app.services.node_code_resolution import resolve_dispatch_node_code
from server.app.services.node_codes import NodeCodeService
from server.app.services.versioned_entities import VersionedEntityStore


def _archive_global_codes(job_db) -> None:
    store = VersionedEntityStore(job_db.path, "node_code")
    for node_key in ("intake_knowledge_points", "publish_content"):
        store.archive_all(f"{DEMO_WORKFLOW_KEY}:{node_key}", None)


def test_conftest_seeded_global_demo_codes(job_db) -> None:
    service = NodeCodeService(job_db.path)
    for node_key in ("intake_knowledge_points", "publish_content"):
        row = service.get_global_published(DEMO_WORKFLOW_KEY, node_key)
        assert row is not None
        assert row["version"] == 1
        assert "def run(" in row["code"]


def test_seed_is_absent_only(job_db, settings) -> None:
    # conftest already seeded; re-seeding is a no-op (no new version).
    assert seed_demo_node_codes(settings) == []
    row = NodeCodeService(job_db.path).get_global_published(
        DEMO_WORKFLOW_KEY, "intake_knowledge_points"
    )
    assert row is not None and row["version"] == 1


def test_seed_does_not_resurrect_archived_codes_and_respects_gate(job_db, settings) -> None:
    # Seed-if-absent mirrors the executor catalog seed: an entity key with any
    # version history (even fully archived) is never resurrected.
    _archive_global_codes(job_db)
    assert seed_demo_node_codes(settings) == []
    assert (
        NodeCodeService(job_db.path).get_global_published(
            DEMO_WORKFLOW_KEY, "intake_knowledge_points"
        )
        is None
    )

    # Gate off: no writes either way.
    settings.executor_runtime.workflows.custom_nodes_enabled = False
    assert seed_demo_node_codes(settings) == []


def test_dispatch_resolves_global_seed_without_workspace_code(job_db, settings) -> None:
    seed_demo_node_codes(settings)

    code = resolve_dispatch_node_code(
        job_db.path, True, "any-workspace", DEMO_WORKFLOW_KEY, "intake_knowledge_points", None
    )

    assert code is not None and "knowledge_point.json" in code


def test_dispatch_frozen_pin_reads_global_seed(job_db, settings) -> None:
    seed_demo_node_codes(settings)
    service = NodeCodeService(job_db.path)
    row = service.get_global_published(DEMO_WORKFLOW_KEY, "publish_content")
    assert row is not None

    code = resolve_dispatch_node_code(
        job_db.path,
        True,
        "any-workspace",
        DEMO_WORKFLOW_KEY,
        "publish_content",
        {"version": row["version"], "code_hash": row["code_hash"]},
    )

    assert code == row["code"]
