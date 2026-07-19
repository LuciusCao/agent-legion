"""Permanent architecture ratchets for Workspace Executor governance.

These tests scan production source files for legacy executor/concurrency patterns
that Phase 5 removed and verify that the legacy module paths stay deleted.
"""

import importlib
import re
from collections.abc import Iterable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Directories that contain production source code to scan.
SCAN_DIRS: tuple[Path, ...] = (
    ROOT / "server",
    ROOT / "frontend" / "src",
    ROOT / "config" / "workflows",
)

# Files and directories that legitimately reference legacy concepts.
WHITELIST: set[str] = {
    # Versioned migrations reference tables/columns being removed.
    str(ROOT / "server" / "app" / "db" / "migrations"),
    # Legacy migration module is intentionally transitional.
    str(ROOT / "server" / "app" / "executors" / "legacy_migration.py"),
    # Bootstrap state helper is transitional until all databases are past V005.
    str(ROOT / "server" / "app" / "jobs" / "executor_configuration.py"),
    # Pipeline definition loader intentionally rejects removed 'runner'/'agent' fields.
    str(ROOT / "server" / "app" / "workflows" / "definition.py"),
    str(ROOT / "server" / "app" / "workflows" / "loader.py"),
    # Video pipeline runner uses openclaw command template strings.
    str(ROOT / "server" / "app" / "pipeline" / "runners.py"),
    # Comment-only reference to the removed table.
    str(ROOT / "server" / "app" / "services" / "workspace_executor_warnings.py"),
    # Job detail panel picks the new NodeRunResponse.runner API field (run provenance).
    str(ROOT / "frontend" / "src" / "components" / "NodeDetailsPanel.tsx"),
}

FORBIDDEN_PATTERNS = {
    "node.runner literal": r"['\"]runner['\"]",
    "node.agent literal": r"['\"]agent['\"]",
    "pipeline_config_json column": "pipeline_config_json",
    "workspace_agent_assignments table": "workspace_agent_assignments",
    "workspace_executor_bootstrap_state table": "workspace_executor_bootstrap_state",
    "WorkflowNode.agent attribute": r"\.agent\b",
    "WorkflowDefinition.concurrency attribute": r"\.concurrency\b",
}


def _is_whitelisted(path: Path) -> bool:
    path_str = str(path)
    for whitelist_path in WHITELIST:
        if path_str == whitelist_path or path_str.startswith(whitelist_path + "/"):
            return True
    return False


def _source_files() -> Iterable[Path]:
    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for path in scan_dir.rglob("*"):
            if not path.is_file() or ".test." in path.name or path.name.startswith("test_"):
                continue
            if path.suffix not in {".py", ".yaml", ".yml", ".ts", ".tsx"}:
                continue
            if _is_whitelisted(path):
                continue
            yield path


def _find_matches(pattern: str) -> list[tuple[str, int, str]]:
    compiled = re.compile(pattern)
    matches: list[tuple[str, int, str]] = []
    for path in _source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                matches.append((path.relative_to(ROOT).as_posix(), lineno, line.strip()))
    return matches


@pytest.mark.parametrize("name", sorted(FORBIDDEN_PATTERNS))
def test_forbidden_pattern_is_absent(name: str) -> None:
    """Legacy executor/concurrency patterns must not reappear in production code."""
    pattern = FORBIDDEN_PATTERNS[name]
    matches = _find_matches(pattern)
    assert not matches, f"Forbidden pattern {name!r} found:\n" + "\n".join(
        f"  {path}:{lineno}: {line}" for path, lineno, line in matches
    )


@pytest.mark.parametrize(
    "module_name",
    [
        "server.app.routes.workspace_agents",
        "server.app.services.workspace_agent_assignments",
    ],
)
def test_legacy_module_is_not_importable(module_name: str) -> None:
    """Legacy Workspace Agent route and service modules are gone."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)
