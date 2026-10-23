"""Tests for scripts/architecture/zero_reference_exports.py (#732).

The fixtures replay the issue's four acceptance samples — the "callers
deleted, implementation left" shape from #328 / skill_version.py / 912c32df7 /
3e4fbda34 — plus the exemption classes (decorated route handlers, __init__
re-export surfaces, export default, generated API types) and the textual
reference convention (yaml/docstring mentions keep a symbol alive).
"""

import json
from pathlib import Path

import pytest

from scripts.architecture.zero_reference_exports import check_zero_reference_exports

pytestmark = pytest.mark.no_db


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_allowlist(root: Path, entries: list[dict[str, str]] | None = None) -> None:
    payload = {"version": 1, "allowlist": entries or []}
    write(
        root / "config/architecture/zero-reference-exports-allowlist.json",
        json.dumps(payload, indent=2) + "\n",
    )


def test_flags_backend_function_whose_callers_were_deleted(tmp_path):
    """skill_version.py shape: the implementation outlives its last caller."""
    write_allowlist(tmp_path)
    write(
        tmp_path / "server/app/services/skill_version.py",
        "def resolve_skill_version(ref):\n    return ref\n",
    )

    errors = check_zero_reference_exports(tmp_path)

    assert any(
        "skill_version.py:1" in error and "resolve_skill_version" in error for error in errors
    )


def test_live_caller_keeps_symbol_alive(tmp_path):
    write_allowlist(tmp_path)
    write(tmp_path / "server/app/services/impl.py", "def helper():\n    return 1\n")
    write(
        tmp_path / "server/app/services/caller.py",
        "from .impl import helper\n\n\ndef run():\n    return helper()\n",
    )

    errors = check_zero_reference_exports(tmp_path)

    assert not any("helper" in error for error in errors)
    # run itself is dead in this fixture — the check is not silenced by a neighbour.
    assert any("caller.py:4" in error and "'run'" in error for error in errors)


def test_references_from_yaml_and_docs_count(tmp_path):
    """Dynamic/out-of-band consumers: a yaml capability string is a reference."""
    write_allowlist(tmp_path)
    write(
        tmp_path / "workflow_nodes/review_keywords.py", "def review_keywords(ctx):\n    return {}\n"
    )
    write(
        tmp_path / "config/workflow.yaml",
        "nodes:\n  review_keywords:\n    capability: review_keywords\n",
    )

    errors = check_zero_reference_exports(tmp_path)

    assert not any("review_keywords" in error for error in errors)


def test_decorated_route_handler_is_exempt(tmp_path):
    """Route decorators register by reference; the name is never imported."""
    write_allowlist(tmp_path)
    write(
        tmp_path / "server/app/routes/widgets.py",
        "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n@router.get('/widgets')\ndef list_widgets():\n    return []\n",
    )

    errors = check_zero_reference_exports(tmp_path)

    assert not any("list_widgets" in error for error in errors)
    assert not any("'router'" in error for error in errors)  # the decorator references it


def test_init_py_reexport_surface_is_not_scanned(tmp_path):
    write_allowlist(tmp_path)
    write(tmp_path / "shared/__init__.py", "def helper():\n    return 1\n")

    assert not check_zero_reference_exports(tmp_path)


def test_frontend_dead_export_flagged_generated_dir_skipped(tmp_path):
    """#328 shadow-layer shape on the frontend, and the generated exemption."""
    write_allowlist(tmp_path)
    write(
        tmp_path / "frontend/src/hooks/useShadowPanel.ts",
        "export function useShadowPanel() {\n  return null;\n}\n",
    )
    write(
        tmp_path / "frontend/src/generated/api.ts", "export interface webhooks {\n  x: string;\n}\n"
    )

    errors = check_zero_reference_exports(tmp_path)

    assert any("useShadowPanel.ts" in error and "useShadowPanel" in error for error in errors)
    assert not any("generated/api.ts" in error for error in errors)


def test_frontend_live_import_and_export_default(tmp_path):
    write_allowlist(tmp_path)
    write(
        tmp_path / "frontend/src/components/Panel.tsx",
        "export function PanelTitle() {\n  return null;\n}\nexport default function Panel() {\n  return null;\n}\n",
    )
    write(
        tmp_path / "frontend/src/App.tsx",
        "import { PanelTitle } from './components/Panel';\nexport const App = PanelTitle;\n",
    )

    errors = check_zero_reference_exports(tmp_path)

    assert not any("PanelTitle" in error for error in errors)
    assert not any("Panel'" in error for error in errors)  # export default is not tracked


def test_allowlist_suppresses_stale_entry_fails(tmp_path):
    write(tmp_path / "server/app/services/dead.py", "def dead_helper():\n    return 1\n")
    entry = {
        "path": "server/app/services/dead.py",
        "symbol": "dead_helper",
        "reason": "tracked cleanup",
    }
    write_allowlist(tmp_path, [entry])

    assert not check_zero_reference_exports(tmp_path)

    write_allowlist(
        tmp_path, [entry, {"path": "server/app/services/dead.py", "symbol": "gone", "reason": "x"}]
    )
    errors = check_zero_reference_exports(tmp_path)
    assert any("stale" in error and "'gone'" in error for error in errors)


def test_allowlist_file_itself_is_not_a_reference(tmp_path):
    """The pin quoting the symbol name must not keep the symbol alive."""
    write_allowlist(tmp_path)  # written first; the entry lands after the def
    write(tmp_path / "server/app/services/dead.py", "def dead_helper():\n    return 1\n")
    write_allowlist(
        tmp_path,
        [
            {
                "path": "server/app/services/other.py",
                "symbol": "dead_helper",
                "reason": "quotes the name",
            }
        ],
    )

    errors = check_zero_reference_exports(tmp_path)

    assert any("dead_helper" in error and "zero-reference export" in error for error in errors)


def test_missing_allowlist_disables_the_check(tmp_path):
    """Whole-repo liveness checks stay off in partial fixture trees."""
    write(tmp_path / "server/app/dead.py", "def dead_helper():\n    return 1\n")

    assert not check_zero_reference_exports(tmp_path)


def test_real_repo_pins_allowlist_config():
    """The real repository must carry the config: deleting it would otherwise
    silently disable the check (the config's presence is what activates it)."""
    root = Path(__file__).resolve().parents[2]

    assert (root / "config/architecture/zero-reference-exports-allowlist.json").is_file()
