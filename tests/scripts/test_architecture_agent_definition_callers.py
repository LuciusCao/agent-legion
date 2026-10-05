"""Tests for the published Agent catalog caller ratchet (#932)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.architecture.agent_definition_callers import check_agent_definition_callers

pytestmark = pytest.mark.no_db

REPO_ROOT = Path(__file__).resolve().parents[2]
_FACADE = "server/app/services/agent_node_profile_catalog.py"
_DEFINER = "server/app/services/agent_service.py"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_baseline(root: Path, files: list[str]) -> None:
    write(
        root / "config/architecture/agent-definition-catalog-callers.json",
        json.dumps(
            {
                "version": 1,
                "symbols": ["published_agent_definitions", "has_published_agent_definitions"],
                "files": files,
            }
        ),
    )


def _seed_facade(root: Path) -> None:
    write(root / _DEFINER, "def published_agent_definitions(dsn, ws):\n    return {}\n")
    write(
        root / _FACADE,
        "from server.app.services.agent_service import published_agent_definitions\n",
    )


def test_repo_has_no_direct_catalog_callers() -> None:
    assert check_agent_definition_callers(REPO_ROOT) == []


def test_new_direct_caller_is_rejected(tmp_path: Path) -> None:
    _seed_facade(tmp_path)
    write(
        tmp_path / "server/app/services/new_reader.py",
        "from server.app.services import agent_service\n"
        "def f(db):\n    return agent_service.published_agent_definitions(db, 'w')\n",
    )
    write_baseline(tmp_path, [_FACADE, _DEFINER])

    errors = check_agent_definition_callers(tmp_path)

    assert len(errors) == 1
    assert errors[0].startswith("server/app/services/new_reader.py:3:")
    assert "agent_node_profile" in errors[0]


def test_import_of_scan_gate_probe_is_rejected(tmp_path: Path) -> None:
    _seed_facade(tmp_path)
    write(
        tmp_path / "server/app/workflow_worker/gate.py",
        "from server.app.services.agent_service import has_published_agent_definitions\n",
    )
    write_baseline(tmp_path, [_FACADE, _DEFINER])

    errors = check_agent_definition_callers(tmp_path)

    assert [e.split(":")[0] for e in errors] == ["server/app/workflow_worker/gate.py"]


def test_stale_baseline_entry_must_be_removed(tmp_path: Path) -> None:
    _seed_facade(tmp_path)
    write(tmp_path / "server/app/services/old_reader.py", "x = 1\n")
    write_baseline(tmp_path, [_FACADE, _DEFINER, "server/app/services/old_reader.py"])

    errors = check_agent_definition_callers(tmp_path)

    assert len(errors) == 1
    assert "stale entry server/app/services/old_reader.py" in errors[0]


def test_missing_baseline_is_a_configuration_error(tmp_path: Path) -> None:
    errors = check_agent_definition_callers(tmp_path)

    assert len(errors) == 1
    assert "Baseline file not found" in errors[0]
