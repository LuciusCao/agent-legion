"""Unmapped-path and new-route policy for affected-test selection."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_UNCOVERED_BACKEND_PREFIXES = ("config/", "deploy/", "examples/")
_ROUTE_INVENTORY_TEST = (
    "tests/routes/test_studio_agent_scope.py::"
    "test_every_write_endpoint_guarded_or_explicitly_exempt"
)


def _is_test_module(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") and name.endswith(".py")


def new_route_inventory_test(path: str, root: Path) -> str | None:
    if not path.startswith("server/app/routes/") or not path.endswith(".py"):
        return None
    if not (root / path).is_file():
        return None
    try:
        tracked = (
            subprocess.run(
                ["git", "ls-files", "--error-unmatch", "--", path],
                cwd=root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            == 0
        )
    except OSError:
        tracked = False
    return None if tracked else _ROUTE_INVENTORY_TEST


def unmapped_source_files(
    changed: list[str], mapping: dict[str, list[str]], repo_root: Path | None = None
) -> list[str]:
    """Changed paths whose unknown impact requires the full unit fallback."""
    root = repo_root if repo_root is not None else REPO_ROOT
    unmapped: list[str] = []
    for path in changed:
        if path.startswith(_UNCOVERED_BACKEND_PREFIXES):
            unmapped.append(path)
            continue
        if path.startswith("tests/"):
            if not _is_test_module(path) and path.endswith(".py"):
                unmapped.append(path)
            continue
        if new_route_inventory_test(path, root) is not None:
            continue
        if mapping.get(path) is None and (root / path).is_file():
            unmapped.append(path)
    return sorted(unmapped)
