"""Worker image isolation: ``worker/`` must never import ``server/``.

The worker Docker image ships only ``worker/`` + ``shared/``; a reverse
import of server internals used to crash-loop the executor at runtime
(issue #16). This static check fails at test time instead, and pins the
Dockerfile's whole-directory COPY + build-time smoke import so the COPY
manifest cannot rot back into a per-file list.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.no_db
def test_worker_modules_do_not_import_server() -> None:
    offenders = []
    for path in sorted((REPO_ROOT / "worker").glob("*.py")):
        roots = _imported_roots(path)
        if "server" in roots:
            offenders.append(path.name)
    assert not offenders, f"worker modules importing server: {offenders}"


@pytest.mark.no_db
def test_shared_modules_are_stdlib_only() -> None:
    allowed = set(sys.stdlib_module_names) | {"shared"}
    for path in sorted((REPO_ROOT / "shared").glob("*.py")):
        roots = _imported_roots(path) - allowed
        assert not roots, f"{path.name} imports third-party/local roots: {roots}"


@pytest.mark.no_db
def test_worker_image_copies_whole_dirs_and_smoke_imports() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY worker /app/worker" in dockerfile
    assert "COPY shared /app/shared" in dockerfile
    # No per-file server COPY manifest may come back.
    assert "COPY server/" not in dockerfile
    assert 'python3 -c "import worker.service' in dockerfile
