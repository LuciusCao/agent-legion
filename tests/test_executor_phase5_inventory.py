"""Phase 5 completion inventory.

This test verifies that the legacy Workflow definition symbols removed in Phase 5
are no longer present and that the replacement mechanisms are in place.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

LEGACY_PATHS: dict[str, tuple[str, str]] = {
    "workflow runner": ("server/app/workflows/definition.py", "RunnerKind"),
    "workflow concurrency": ("server/app/workflows/definition.py", "PipelineConcurrency"),
}

REPLACEMENTS: dict[str, str] = {
    "workflow runner": "Node capability + Executor binding kind",
    "workflow concurrency": "Workspace-level Executor allocation limits + local Node limits",
}

PROTECTED_VIDEO_HIVE_PATHS: set[str] = {
    "server/app/routes/agents.py",
    "server/app/agents.py",
    "server/app/pipeline/openclaw.py",
    "server/app/worker.py",
}


def _read_source(rel_path: str) -> str:
    path = ROOT / rel_path
    if not path.exists():
        pytest.fail(f"Inventory source file is missing: {rel_path}")
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(LEGACY_PATHS))
def test_legacy_path_is_absent(name: str) -> None:
    """Each legacy symbol has been removed from the codebase."""
    rel_path, token = LEGACY_PATHS[name]
    source = _read_source(rel_path)
    assert token not in source, f"Legacy token {token!r} still present in {rel_path}"


def test_inventory_includes_replacement_for_every_legacy_path() -> None:
    """The inventory is complete: every legacy path names a replacement."""
    assert set(LEGACY_PATHS) == set(REPLACEMENTS)


# Concrete replacement tokens that demonstrate the legacy concepts were replaced.
REPLACEMENT_TOKENS: dict[str, list[tuple[str, str]]] = {
    "workflow runner": [
        ("server/app/workflows/schema.py", "capability"),
        ("server/app/workflow_worker_schedule.py", "workspace_node_bindings"),
    ],
    "workflow concurrency": [
        ("server/app/db/migrations/v001_executor_core.py", "workspace_executor_allocations"),
        ("server/app/db/migrations/v001_executor_core.py", "workspace_node_limits"),
    ],
}


@pytest.mark.parametrize("name", sorted(REPLACEMENT_TOKENS))
def test_replacement_tokens_are_present(name: str) -> None:
    """Each legacy path's replacement mechanism exists in the codebase."""
    for rel_path, token in REPLACEMENT_TOKENS[name]:
        source = _read_source(rel_path)
        assert token in source, f"Replacement token {token!r} missing from {rel_path}"


def test_workspace_agent_assignment_modules_are_removed() -> None:
    """Legacy workspace agent assignment route and service are gone."""
    assert not (ROOT / "server/app/routes/workspace_agents.py").exists()
    assert not (ROOT / "server/app/services/workspace_agent_assignments.py").exists()


@pytest.mark.parametrize("rel_path", sorted(PROTECTED_VIDEO_HIVE_PATHS))
def test_protected_video_hive_path_exists(rel_path: str) -> None:
    """These Video Hive files must still exist at Phase 5 completion."""
    assert (ROOT / rel_path).exists(), f"Protected Video Hive path missing: {rel_path}"
