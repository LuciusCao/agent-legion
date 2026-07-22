"""Phase 5 architecture ratchets for Workspace Executor governance.

These checks prevent reintroduction of legacy Pipeline runner/concurrency fields
and legacy Workspace Agent assignment modules.
"""

import re
from pathlib import Path

_FORBIDDEN_PATTERNS = {
    "node.runner literal": re.compile(r"['\"]runner['\"]"),
    "pipeline_config_json column": re.compile("pipeline_config_json"),
    "workspace_agent_assignments table": re.compile("workspace_agent_assignments"),
    "workspace_executor_bootstrap_state table": re.compile("workspace_executor_bootstrap_state"),
    "WorkflowNode.agent attribute": re.compile(r"\.agent\b"),
    "WorkflowDefinition.concurrency attribute": re.compile(r"\.concurrency\b"),
}

_FORBIDDEN_PATTERN_WHITELIST = {
    "server/app/db/migrations",
    "server/app/executors/legacy_migration.py",
    "server/app/jobs/executor_configuration.py",
    "server/app/workflows/definition.py",
    "server/app/workflows/loader.py",
    "server/app/pipeline/runners.py",
    "server/app/services/workspace_executor_warnings.py",
    # AgentStatusManager payloads key incremental WS envelopes by "agent" (Decision 8
    # of the phase4 agent-collaboration plan); the literal is its own status domain,
    # not a legacy Workflow node declaration.
    "server/app/agents.py",
    # Same WS envelope domain on the client: parses agent_busy/agent_idle payloads.
    "frontend/src/stores/agentsWsMessages.ts",
    # Job detail panel picks the new NodeRunResponse.runner API field (run provenance).
    "frontend/src/components/NodeDetailsPanel.tsx",
}

_LEGACY_MODULES = (
    "server/app/routes/workspace_agents.py",
    "server/app/services/workspace_agent_assignments.py",
)


def _is_whitelisted_for_pattern(rel_path: str) -> bool:
    for whitelist in _FORBIDDEN_PATTERN_WHITELIST:
        if rel_path == whitelist or rel_path.startswith(whitelist + "/"):
            return True
    return False


def check_legacy_modules_absent(root: Path) -> list[str]:
    """Fail if legacy Workspace Agent route/service modules still exist."""
    errors: list[str] = []
    for rel_path in _LEGACY_MODULES:
        if (root / rel_path).exists():
            errors.append(f"{rel_path}: legacy module must be removed")
    return errors


def check_forbidden_patterns(root: Path) -> list[str]:
    """Scan production source for legacy executor/concurrency string patterns."""
    errors: list[str] = []
    scan_dirs = [root / "server", root / "frontend" / "src", root / "config" / "workflows"]
    for scan_dir in scan_dirs:
        if not scan_dir.is_dir():
            continue
        for path in sorted(scan_dir.rglob("*")):
            if not path.is_file() or ".test." in path.name or path.name.startswith("test_"):
                continue
            if path.suffix not in {".py", ".yaml", ".yml", ".ts", ".tsx"}:
                continue
            rel_path = path.relative_to(root).as_posix()
            if _is_whitelisted_for_pattern(rel_path):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for name, pattern in _FORBIDDEN_PATTERNS.items():
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if pattern.search(line):
                        errors.append(f"{rel_path}:{lineno}: forbidden pattern {name!r}")
    return errors
