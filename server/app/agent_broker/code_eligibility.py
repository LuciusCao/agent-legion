"""Worker-eligibility static scan for code-executor node payloads (batch 2).

A code payload may be routed to a remote Worker only when its transitive
import closure stays within what the bundle ships (``workspace_libs``) plus
the Worker image's stdlib and preinstalled third-party packages — the same
rule the C1 self-containment contract test enforces on the builtin nodes
(design §7.2). Nodes importing ``server.*`` (e.g. the three video heavy
nodes) are not eligible and stay on Host-local execution.

Results are cached by content hash: dispatch re-scans the same builtin file
on every poll pass otherwise.
"""

from __future__ import annotations

import ast
import hashlib
import sys
import threading
from pathlib import Path

# Repo-local roots the bundle actually ships (the node code text itself rides
# as a separate file, so ``workflow_nodes`` is deliberately NOT allowed: a
# cross-node import would be missing on the Worker). Third-party roots are
# preinstalled in the Worker image and importable inside the sandbox.
_ALLOWED_REPO_ROOTS = frozenset({"workspace_libs"})
_ALLOWED_THIRD_PARTY_ROOTS = frozenset({"requests"})

_CACHE_LIMIT = 1024
_cache: dict[str, bool] = {}
_cache_lock = threading.Lock()


def _imports(tree: ast.Module) -> list[tuple[str, int, list[str]]]:
    """Return (module, level, from-names) for every static import in tree."""
    found: list[tuple[str, int, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, 0, []) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, node.level, [alias.name for alias in node.names]))
    return found


def _has_dynamic_import(tree: ast.Module) -> bool:
    for element in ast.walk(tree):
        if isinstance(element, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in element.names]
            if isinstance(element, ast.ImportFrom):
                names.append(element.module or "")
            if any(name.split(".")[0] == "importlib" for name in names):
                return True
        elif isinstance(element, ast.Call):
            if isinstance(element.func, ast.Name) and element.func.id == "__import__":
                return True
    return False


def _resolve(repo_root: Path, module: str) -> Path | None:
    """Resolve a workspace_libs dotted module to its source file, if any."""
    parts = module.split(".")
    package = repo_root.joinpath(*parts, "__init__.py")
    if package.is_file():
        return package
    source = repo_root.joinpath(*parts).with_suffix(".py")
    if source.is_file():
        return source
    return None


def _scan(code_text: str, repo_root: Path) -> bool:
    try:
        entry_tree = ast.parse(code_text)
    except SyntaxError:
        return False
    if _has_dynamic_import(entry_tree):
        return False
    allowed = set(sys.stdlib_module_names) | _ALLOWED_REPO_ROOTS | _ALLOWED_THIRD_PARTY_ROOTS
    visited: set[Path] = set()
    # (tree, path) pairs; the entry code has no file, its workspace_libs
    # imports resolve against the repo tree like any package module.
    stack: list[tuple[ast.Module, Path | None]] = [(entry_tree, None)]
    while stack:
        tree, path = stack.pop()
        if path is not None:
            if path in visited:
                continue
            visited.add(path)
        for module, level, names in _imports(tree):
            if level != 0:
                continue
            root = module.split(".")[0]
            if root not in allowed:
                return False
            if root in _ALLOWED_REPO_ROOTS:
                candidates = [_resolve(repo_root, module)]
                # ``from package import submodule`` may reference a submodule
                # the package __init__ does not re-export; resolve it too.
                candidates.extend(_resolve(repo_root, f"{module}.{name}") for name in names)
                for resolved in candidates:
                    if resolved is not None and resolved not in visited:
                        try:
                            subtree = ast.parse(resolved.read_text(encoding="utf-8"))
                        except (OSError, SyntaxError):
                            return False
                        if _has_dynamic_import(subtree):
                            return False
                        stack.append((subtree, resolved))
    return True


def is_worker_eligible(code_text: str, repo_root: Path) -> bool:
    """True when the code's import closure fits the Worker bundle contract."""
    digest = hashlib.sha256(code_text.encode("utf-8")).hexdigest()
    with _cache_lock:
        cached = _cache.get(digest)
    if cached is not None:
        return cached
    eligible = _scan(code_text, repo_root)
    with _cache_lock:
        if len(_cache) >= _CACHE_LIMIT:
            _cache.clear()
        _cache[digest] = eligible
    return eligible
