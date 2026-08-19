"""Architecture guards against reintroduction of legacy video runtime dependencies."""

# ruff: noqa: SIM905

import ast
from pathlib import Path

from scripts.architecture.helpers import forbidden_imports, imported_modules

_VIDEO_CAPABILITIES_ROOT = "server/app/video_capabilities"

_VIDEO_CAPABILITIES_FORBIDDEN = tuple(
    """server.app.db server.app.jobs server.app.routes server.app.worker server.app.worker_thread""".split()
)

_LEGACY_VIDEO_ROUTE_MODULES = tuple(
    """server.app.routes.videos server.app.routes.video_hive""".split()
)

_WORKSPACE_MODULE_PREFIXES = tuple(
    """server/app/routes/jobs.py server/app/routes/job_artifacts.py server/app/routes/job_batches.py server/app/routes/workspace_ server/app/services/job_ server/app/services/workspace_ server/app/services/executor_catalog.py server/app/services/workflow_definitions.py""".split()
)

_PIPELINE_PHASES_MODULE = "server.app.pipeline.phases"


def _is_workspace_module(rel_path: str) -> bool:
    return any(
        rel_path == prefix or rel_path.startswith(prefix) for prefix in _WORKSPACE_MODULE_PREFIXES
    )


def _source_files(root: Path, *globs: str) -> list[Path]:
    paths: list[Path] = []
    for pattern in globs:
        paths.extend(root.glob(pattern))
    return sorted(paths)


def _resolve_relative_import(rel_path: str, module: str | None, level: int) -> str | None:
    """Resolve a relative import to its absolute module name based on the source path."""
    if level == 0 or module is None:
        return module
    parts = rel_path.replace("/", ".").split(".")[:-1]
    if len(parts) < level:
        return None
    package_parts = parts[:-level]
    if module:
        package_parts.append(module)
    return ".".join(package_parts)


def _all_imported_modules(rel_path: str, tree: ast.AST) -> dict[str, int]:
    """Return imported module names, resolving relative imports to absolute names."""
    modules = imported_modules(tree)
    resolved: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0 or not node.module:
            continue
        absolute = _resolve_relative_import(rel_path, node.module, node.level)
        if absolute is None:
            continue
        resolved[absolute] = node.lineno
        for alias in node.names:
            resolved[f"{absolute}.{alias.name}"] = node.lineno
    # Absolute imports take precedence when line numbers collide.
    resolved.update(modules)
    return resolved


def check_video_capabilities_import_boundaries(root: Path) -> list[str]:
    """video_capabilities modules must not import orchestration/runtime modules."""
    errors: list[str] = []
    for path in _source_files(root, f"{_VIDEO_CAPABILITIES_ROOT}/**/*.py"):
        rel_path = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
        except SyntaxError as exc:
            errors.append(f"{rel_path}: syntax error ({exc})")
            continue
        modules = _all_imported_modules(rel_path, tree)
        for module, lineno in forbidden_imports(modules, _VIDEO_CAPABILITIES_FORBIDDEN):
            errors.append(
                f"{rel_path}:{lineno}: video_capabilities boundary forbids import {module!r}"
            )
    return errors


def check_legacy_video_route_imports(root: Path) -> list[str]:
    """Production code must not reintroduce legacy video route modules."""
    errors: list[str] = []
    for path in _source_files(root, "server/app/**/*.py"):
        rel_path = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
        except SyntaxError as exc:
            errors.append(f"{rel_path}: syntax error ({exc})")
            continue
        modules = _all_imported_modules(rel_path, tree)
        for module, lineno in forbidden_imports(modules, _LEGACY_VIDEO_ROUTE_MODULES):
            errors.append(
                f"{rel_path}:{lineno}: legacy video route import {module!r} "
                "after migration; use Workspace job routes instead"
            )
    return errors


def check_video_legacy(root: Path) -> list[str]:
    """Run all legacy video reintroduction guards."""
    return (
        check_video_capabilities_import_boundaries(root)
        + check_legacy_video_route_imports(root)
        + check_workspace_pipeline_phase_imports(root)
    )


def check_workspace_pipeline_phase_imports(root: Path) -> list[str]:
    """Workspace modules must not import the legacy pipeline phases module."""
    errors: list[str] = []
    for path in _source_files(root, "server/app/**/*.py"):
        rel_path = path.relative_to(root).as_posix()
        if not _is_workspace_module(rel_path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
        except SyntaxError as exc:
            errors.append(f"{rel_path}: syntax error ({exc})")
            continue
        modules = _all_imported_modules(rel_path, tree)
        for module, lineno in modules.items():
            if module == _PIPELINE_PHASES_MODULE or module.startswith(
                f"{_PIPELINE_PHASES_MODULE}."
            ):
                errors.append(
                    f"{rel_path}:{lineno}: Workspace module imports legacy pipeline phase "
                    f"module {module!r}; use workflow capabilities instead"
                )
    return errors
