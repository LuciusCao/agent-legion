"""Node self-containment: worker-eligible nodes must be import-self-contained.

Batch 2 routes code-executor nodes to remote workers by shipping the code
text plus a ``workspace_libs`` snapshot; the worker has no repo checkout. A
node is worker-eligible only when its transitive import closure stays within
``workspace_libs`` + stdlib — the same rule the custom-node sandbox enforces
(see docs/architecture/node-sdk-and-worker-execution-design.md §7.2).

The three video heavy nodes are explicitly exempt: they import
``server.app.pipeline.*`` (ffmpeg subprocess, ASR providers) and stay on
Host local execution for batch 2 (design doc §7.2).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

SELF_CONTAINED_NODES = (
    "question_intake",
    "question_clean_parse",
    "comprehension_classify",
    "comprehension_assemble",
    "comprehension_finalize",
    "video_package",
    # Demo workflow nodes: pure stdlib + node SDK.
    "example_intake",
    "example_publish",
)

# Exempt: depend on server.app.pipeline.* (ffmpeg subprocess / ASR providers)
# and stay on Host local execution (design doc §7.2); not worker-eligible.
EXEMPT_NODES = ("video_download", "video_assemble", "video_transcribe")

# Repo-local roots a worker-eligible node may pull into its closure.
_ALLOWED_REPO_ROOTS = frozenset({"workspace_libs", "workflow_nodes"})

# Third-party roots allowed in the closure: preinstalled in the worker image
# (Dockerfile pip install) and already importable inside the custom-node
# sandbox (site-packages stay on the sandbox read allowlist).
_ALLOWED_THIRD_PARTY_ROOTS = frozenset({"requests"})


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(path: Path) -> list[tuple[str, int, list[str]]]:
    """Return (module, level, from-names) for every static import in path."""
    found: list[tuple[str, int, list[str]]] = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            found.extend((alias.name, 0, []) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, node.level, [alias.name for alias in node.names]))
    return found


def _resolve(module: str) -> Path | None:
    """Resolve a repo-local dotted module to its source file, if it exists."""
    parts = module.split(".")
    package = REPO_ROOT.joinpath(*parts, "__init__.py")
    if package.is_file():
        return package
    source = REPO_ROOT.joinpath(*parts).with_suffix(".py")
    if source.is_file():
        return source
    return None


def _import_closure(entry: Path) -> set[str]:
    """Return every import root in the transitive repo-local closure of entry."""
    roots: set[str] = set()
    visited: set[Path] = set()
    stack = [entry]
    while stack:
        path = stack.pop()
        if path in visited:
            continue
        visited.add(path)
        for module, level, names in _imports(path):
            if level != 0:
                continue
            root = module.split(".")[0]
            roots.add(root)
            if root in _ALLOWED_REPO_ROOTS:
                resolved = _resolve(module)
                if resolved is not None:
                    stack.append(resolved)
                # ``from package import submodule`` may reference a submodule
                # the package __init__ does not re-export; resolve it too.
                for name in names:
                    resolved = _resolve(f"{module}.{name}")
                    if resolved is not None:
                        stack.append(resolved)
    return roots


@pytest.mark.no_db
def test_node_inventory_is_explicitly_classified() -> None:
    node_files = {
        p.stem for p in (REPO_ROOT / "workflow_nodes").glob("*.py") if p.stem != "__init__"
    }
    classified = set(SELF_CONTAINED_NODES) | set(EXEMPT_NODES)
    assert node_files == classified, (
        "every workflow_nodes file must be classified as self-contained or exempt: "
        f"unclassified={sorted(node_files - classified)}, "
        f"stale={sorted(classified - node_files)}"
    )


@pytest.mark.no_db
@pytest.mark.parametrize("node", SELF_CONTAINED_NODES)
def test_self_contained_node_import_closure(node: str) -> None:
    entry = REPO_ROOT / "workflow_nodes" / f"{node}.py"
    allowed = set(sys.stdlib_module_names) | _ALLOWED_REPO_ROOTS | _ALLOWED_THIRD_PARTY_ROOTS
    offenders = _import_closure(entry) - allowed
    assert not offenders, f"{node}: import closure escapes workspace_libs + stdlib: {offenders}"


@pytest.mark.no_db
@pytest.mark.parametrize("node", SELF_CONTAINED_NODES)
def test_self_contained_node_has_no_dynamic_import(node: str) -> None:
    entry = REPO_ROOT / "workflow_nodes" / f"{node}.py"
    tree = _parse(entry)
    for element in ast.walk(tree):
        if isinstance(element, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in element.names]
            if isinstance(element, ast.ImportFrom):
                names.append(element.module or "")
            assert not any(n.split(".")[0] == "importlib" for n in names), (
                f"{node}: dynamic import via importlib defeats the static scan"
            )
        elif isinstance(element, ast.Call):
            assert not (isinstance(element.func, ast.Name) and element.func.id == "__import__"), (
                f"{node}: dynamic import via __import__ defeats the static scan"
            )


@pytest.mark.no_db
@pytest.mark.parametrize("node", EXEMPT_NODES)
def test_exempt_node_still_imports_server(node: str) -> None:
    # The exemption is only justified while the node depends on Host-only
    # modules; if that changes, reclassify it as self-contained instead.
    entry = REPO_ROOT / "workflow_nodes" / f"{node}.py"
    assert "server" in _import_closure(entry), (
        f"{node} no longer imports server.* — move it to SELF_CONTAINED_NODES"
    )
