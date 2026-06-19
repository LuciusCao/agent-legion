import ast
from importlib.util import resolve_name
from pathlib import Path

from scripts.architecture.helpers import imported_modules, is_service_path


def check_service_import_boundaries(relative_path: str, tree: ast.AST) -> list[str]:
    if not is_service_path(relative_path):
        return []
    errors: list[str] = []
    package = ".".join(Path(relative_path).parent.parts)
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.ImportFrom)
            or not node.level
            or node.level > package.count(".") + 1
        ):
            continue
        resolved = resolve_name("." * node.level + (node.module or ""), package)
        imports_worker = resolved == "server.app.worker" or resolved.startswith(
            "server.app.worker."
        )
        imports_worker = imports_worker or (
            node.module is None
            and resolved == "server.app"
            and any(alias.name == "worker" for alias in node.names)
        )
        if imports_worker:
            errors.append(
                f"{relative_path}:{node.lineno}: service boundary forbids import server.app.worker"
            )
    for module, lineno in imported_modules(tree).items():
        if (
            module == "fastapi"
            or module.startswith("fastapi.")
            or module == "server.app.worker"
            or module.startswith("server.app.worker.")
        ):
            errors.append(f"{relative_path}:{lineno}: service boundary forbids import {module}")
    return errors
