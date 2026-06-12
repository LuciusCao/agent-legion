"""Phase 5 compatibility inventory.

This test freezes the set of legacy paths that still exist at the start of
Phase 5 and the Video Hive paths that must survive the phase.  It only reads
the source files listed in the inventory, so it is safe to run before any
production changes.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

LEGACY_PATHS: dict[str, tuple[str, str]] = {
    "pipeline runner": ("server/app/pipelines/definition.py", "RunnerKind"),
    "pipeline concurrency": ("server/app/pipelines/definition.py", "PipelineConcurrency"),
    "agent route": ("server/app/routes/workspace_agents.py", "/agents"),
}

REPLACEMENTS: dict[str, str] = {
    "pipeline runner": "Node capability + Executor binding kind",
    "pipeline concurrency": "Workspace-level Executor allocation limits + local Node limits",
    "agent route": "Workspace Executor configuration routes",
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
def test_legacy_path_is_present(name: str) -> None:
    """Each legacy symbol or route still exists before Phase 5 removal."""
    rel_path, token = LEGACY_PATHS[name]
    source = _read_source(rel_path)
    assert token in source, f"Legacy token {token!r} not found in {rel_path}"


def test_inventory_includes_replacement_for_every_legacy_path() -> None:
    """The inventory is complete: every legacy path names a replacement."""
    assert set(LEGACY_PATHS) == set(REPLACEMENTS)


@pytest.mark.parametrize("rel_path", sorted(PROTECTED_VIDEO_HIVE_PATHS))
def test_protected_video_hive_path_exists(rel_path: str) -> None:
    """These Video Hive files must still exist at Phase 5 completion."""
    assert (ROOT / rel_path).exists(), f"Protected Video Hive path missing: {rel_path}"
