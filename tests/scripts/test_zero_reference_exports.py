"""Tests for scripts/architecture/zero_reference_exports.py (#732).

The fixtures replay the issue's four acceptance samples — the "callers
deleted, implementation left" shape from #328 / skill_version.py / 912c32df7 /
3e4fbda34 — plus the exemption classes (decorated route handlers, __init__
re-export surfaces, export default, generated API types) and the textual
reference convention (yaml/docstring mentions keep a symbol alive).
"""

import json
import os
from pathlib import Path

import pytest

from scripts.architecture import zero_reference_exports
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


@pytest.mark.parametrize(
    ("decorator", "definition"),
    [
        ("dataclass", "class DeadRecord:\n    value: int\n"),
        ("functools.lru_cache(maxsize=None)", "def dead_cached():\n    return 1\n"),
        ("contextlib.contextmanager", "def dead_context():\n    yield None\n"),
    ],
)
def test_transforming_decorators_do_not_hide_dead_definitions(tmp_path, decorator, definition):
    """#1375: only registration decorators exempt a zero-reference symbol."""
    write_allowlist(tmp_path)
    write(
        tmp_path / "server/app/services/decorated.py",
        f"import contextlib\nimport functools\nfrom dataclasses import dataclass\n\n"
        f"@{decorator}\n{definition}",
    )

    errors = check_zero_reference_exports(tmp_path)

    dead_name = definition.split()[1].split("(")[0].rstrip(":")
    assert any(dead_name in error for error in errors)


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


def test_frontend_barrel_reexport_does_not_keep_dead_export_alive(tmp_path):
    """#1380: source + barrel contributes two declaration tokens, not a caller."""
    write_allowlist(tmp_path)
    write(tmp_path / "frontend/src/source.ts", "export interface DeadThing { x: string }\n")
    write(tmp_path / "frontend/src/barrel.ts", "export { DeadThing } from './source';\n")

    errors = check_zero_reference_exports(tmp_path)

    assert sum("DeadThing" in error for error in errors) == 2


def test_frontend_consumer_beyond_source_and_barrel_keeps_export_alive(tmp_path):
    write_allowlist(tmp_path)
    write(tmp_path / "frontend/src/source.ts", "export interface LiveThing { x: string }\n")
    write(tmp_path / "frontend/src/barrel.ts", "export { LiveThing } from './source';\n")
    write(
        tmp_path / "frontend/src/consumer.ts",
        "import type { LiveThing } from './barrel';\nexport const value: LiveThing = { x: 'x' };\n",
    )

    errors = check_zero_reference_exports(tmp_path)

    assert not any("LiveThing" in error for error in errors)


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


@pytest.mark.parametrize(
    "skip_dir",
    [
        ".git",
        ".venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".uv-cache",
        ".tower",
        ".worktrees",
        "__pycache__",
        "node_modules",
        "dist",
        "coverage",
        "target",
        "data",
    ],
)
def test_reference_walk_does_not_enter_or_stat_skip_dirs(tmp_path, monkeypatch, skip_dir):
    visible = tmp_path / "src/nested/visible.py"
    hidden = tmp_path / "src/nested" / skip_dir / "deeper/hidden.py"
    write(visible, "visible_token\n")
    write(hidden, "hidden_token\n")
    scanned_dirs = []
    statted_paths = []
    real_scandir = os.scandir
    real_is_file = Path.is_file

    def observing_scandir(path):
        scanned_dirs.append(Path(path))
        return real_scandir(path)

    def observing_is_file(path):
        statted_paths.append(path)
        return real_is_file(path)

    monkeypatch.setattr(os, "scandir", observing_scandir)
    monkeypatch.setattr(Path, "is_file", observing_is_file)

    assert list(zero_reference_exports._iter_reference_files(tmp_path)) == [visible]
    assert not [
        path
        for path in [*scanned_dirs, *statted_paths]
        if skip_dir in path.relative_to(tmp_path).parts
    ]
    assert tmp_path / "src/nested" in scanned_dirs


def test_reference_files_preserve_global_order_filters_and_similar_names(tmp_path):
    included = [
        ".towerlike/keep.py",
        ".venv-tools/keep.py",
        ".worktrees-archive/keep.py",
        "a/inside.py",
        "a.py",
        "config/workflow.yaml",
        "data.py",
        "database/keep.py",
        "docs/guide.MD",
        "nested/data_factory/keep.py",
        "node_modules_backup/keep.py",
        "z.py",
    ]
    excluded = [
        "config/architecture/nested-pool-checkout-allowlist.json",
        "config/architecture/zero-reference-exports-allowlist.json",
        "docs/ignored.txt",
        "nested/data/hidden.py",
    ]
    for relative in included:
        write(tmp_path / relative, "included_token\n")
    for relative in excluded:
        write(tmp_path / relative, "excluded_token\n")

    paths = list(zero_reference_exports._iter_reference_files(tmp_path))

    assert [path.relative_to(tmp_path).as_posix() for path in paths] == included
    counts = zero_reference_exports.count_identifier_references(tmp_path)
    assert counts["included_token"] == len(included)
    assert counts["excluded_token"] == 0


def test_reference_counts_preserve_file_symlinks(tmp_path):
    source = tmp_path / "server/app/source.py"
    document = tmp_path / "docs/guide.md"
    alias = tmp_path / "docs/alias.md"
    directory_alias = tmp_path / "linked-docs"
    write(source, "source_token\n")
    write(document, "shared_doc_token\n")
    write(tmp_path / "docs/ignored.txt", "ignored_text_token\n")
    alias.symlink_to(document)
    directory_alias.symlink_to(tmp_path / "docs", target_is_directory=True)

    paths = list(zero_reference_exports._iter_reference_files(tmp_path))
    counts = zero_reference_exports.count_identifier_references(tmp_path)

    assert paths == [alias, document, source]
    assert counts["source_token"] == 1
    assert counts["shared_doc_token"] == 2
    assert counts["ignored_text_token"] == 0


def test_real_repo_pins_allowlist_config():
    """The real repository must carry the config: deleting it would otherwise
    silently disable the check (the config's presence is what activates it)."""
    root = Path(__file__).resolve().parents[2]

    assert (root / "config/architecture/zero-reference-exports-allowlist.json").is_file()
