import shutil
from pathlib import Path

AGENT_WORKSPACE_DIRS = {
    ".openclaw",
    "memory",
}

AGENT_WORKSPACE_FILES = {
    "AGENTS.md",
    "BOOTSTRAP.md",
    "HEARTBEAT.md",
    "IDENTITY.md",
    "MEMORY.md",
    "SOUL.md",
    "TOOLS.md",
    "USER.md",
}


def cleanup_agent_workspace_files(path: Path) -> list[Path]:
    """Remove known agent workspace files from a processing directory."""
    removed: list[Path] = []
    if not path.exists() or not path.is_dir():
        return removed

    for name in AGENT_WORKSPACE_FILES:
        target = path / name
        if target.is_file():
            target.unlink()
            removed.append(target)

    for name in AGENT_WORKSPACE_DIRS:
        target = path / name
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(target)

    return removed
