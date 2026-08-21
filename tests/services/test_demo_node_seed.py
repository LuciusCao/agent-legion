"""Workspace-scoped factory seed and legacy-global migration for demo code."""

from __future__ import annotations

from server.app.services.demo_node_migration import migrate_demo_node_codes_to_workspaces
from server.app.services.demo_node_seed import DEMO_WORKFLOW_KEY, seed_demo_workspace_node_codes
from server.app.services.node_code_resolution import resolve_dispatch_node_code
from server.app.services.node_codes import NodeCodeService

NODE_KEYS = ("intake_knowledge_points", "publish_content")


def _workspace(job_db, name: str) -> str:
    return str(job_db.create_workspace(name, default_workflow_key=DEMO_WORKFLOW_KEY)["id"])


def test_seed_publishes_workspace_versions_only(job_db, settings) -> None:
    workspace_id = _workspace(job_db, "demo")

    assert seed_demo_workspace_node_codes(settings, workspace_id) == list(NODE_KEYS)

    service = NodeCodeService(job_db.path)
    for node_key in NODE_KEYS:
        row = service.get_effective_code(workspace_id, DEMO_WORKFLOW_KEY, node_key)
        assert row is not None
        assert row["workspace_id"] == workspace_id
        assert row["version"] == 1
        assert row["created_by"] == "system"
        assert "def run(" in row["code"]
        assert service.get_global_published(DEMO_WORKFLOW_KEY, node_key) is None


def test_seed_is_absent_only_and_respects_gate(job_db, settings) -> None:
    workspace_id = _workspace(job_db, "demo")
    assert seed_demo_workspace_node_codes(settings, workspace_id) == list(NODE_KEYS)
    assert seed_demo_workspace_node_codes(settings, workspace_id) == []

    settings.executor_runtime.workflows.custom_nodes_enabled = False
    other_id = _workspace(job_db, "other")
    assert seed_demo_workspace_node_codes(settings, other_id) == []


def test_dispatch_does_not_cross_workspace_boundary(job_db, settings) -> None:
    seeded_id = _workspace(job_db, "seeded")
    empty_id = _workspace(job_db, "empty")
    seed_demo_workspace_node_codes(settings, seeded_id)

    assert (
        resolve_dispatch_node_code(
            job_db.path,
            True,
            seeded_id,
            DEMO_WORKFLOW_KEY,
            "intake_knowledge_points",
            None,
        )
        is not None
    )
    assert (
        resolve_dispatch_node_code(
            job_db.path,
            True,
            empty_id,
            DEMO_WORKFLOW_KEY,
            "intake_knowledge_points",
            None,
        )
        is None
    )


def test_steady_state_startup_does_not_scan_workspaces(job_db, settings, monkeypatch) -> None:
    def fail_scan() -> None:
        raise AssertionError("workspace scan must be migration-only")

    monkeypatch.setattr(job_db, "list_workspaces", fail_scan)

    assert migrate_demo_node_codes_to_workspaces(settings, job_db) == 0


def test_migration_copies_legacy_global_then_archives_it(job_db, settings) -> None:
    first_id = _workspace(job_db, "first")
    second_id = _workspace(job_db, "second")
    service = NodeCodeService(job_db.path)
    legacy_code = "def run(job, job_dir, runtime):\n    return 'legacy'\n"
    for node_key in NODE_KEYS:
        assert service.seed_global(DEMO_WORKFLOW_KEY, node_key, legacy_code, "legacy seed")

    # An operator-owned workspace version is authoritative and must survive.
    custom_code = "def run(job, job_dir, runtime):\n    return 'custom'\n"
    service.save_draft(
        first_id,
        DEMO_WORKFLOW_KEY,
        "publish_content",
        custom_code,
        "user:admin",
    )
    service.publish(first_id, DEMO_WORKFLOW_KEY, "publish_content")

    assert migrate_demo_node_codes_to_workspaces(settings, job_db) == 3

    for node_key in NODE_KEYS:
        assert service.get_global_published(DEMO_WORKFLOW_KEY, node_key) is None
    assert (
        service.get_effective_code(first_id, DEMO_WORKFLOW_KEY, "intake_knowledge_points")["code"]
        == legacy_code
    )
    assert (
        service.get_effective_code(first_id, DEMO_WORKFLOW_KEY, "publish_content")["code"]
        == custom_code
    )
    for node_key in NODE_KEYS:
        assert (
            service.get_effective_code(second_id, DEMO_WORKFLOW_KEY, node_key)["code"]
            == legacy_code
        )

    # Archived global history remains available to old quality-replay pins.
    archived = service.get_global_code_by_version(DEMO_WORKFLOW_KEY, "intake_knowledge_points", 1)
    assert archived is not None and archived["status"] == "archived"
