"""Workspace boundary ratchets for Workspace Capability Parity.

Protect generic Workspace boundaries, thin routes, and generated transport types.
"""

# ruff: noqa: SIM905

import ast
import re
from pathlib import Path

from scripts.architecture.helpers import imported_modules
from scripts.architecture.route_mutations import (
    check_route_dag_and_deletion as check_route_dag_and_deletion,
)

_WORKSPACE_MODULE_PREFIXES = tuple(
    """server/app/routes/jobs.py server/app/routes/job_artifacts.py server/app/routes/job_batches.py server/app/routes/workspace_ server/app/services/job_ server/app/services/workspace_ server/app/services/agent_catalog_projection.py server/app/services/workflow_definitions.py""".split()
)

_LEGACY_VIDEO_MODULE_PREFIXES = tuple("""server.app.pipeline.""".split())

_LEGACY_VIDEO_EXACT = frozenset({"server.app.pipeline"})
_LEGACY_VIDEO_EXCEPTIONS: set[tuple[str, str]] = set()

_JOB_SERVICE_PREFIX = "server/app/services/job_"
_DIRECT_EXECUTOR_MODULE_PREFIXES = tuple(
    """server.app.executors.code server.app.executors.runtime server.app.executors.contracts""".split()
)

_GENERATED_JOB_TRANSPORT_NAMES = frozenset(
    """ArtifactResponse BatchJobIdsRequest BatchJobMutationResponse BatchRunToRequest ContinueJobRequest DeleteJobResponse ExecutionControlSummaryResponse JobBatchRequest JobBatchRerunRequest JobBatchResponse JobDetailResponse JobLogResponse JobMutationResultResponse JobNodeResponse JobNodeSummaryResponse JobSummaryResponse JobsResponse NodeRunResponse RunToRequest WorkspaceDagResponse WorkspacePackageRequest WorkspacePackageResponse WorkspacePackageResultResponse WorkspaceResponse WorkspaceRunsResponse WorkspaceSettingsResponse WorkspaceSettingsSectionRequest WorkspaceStatsResponse WorkspacesResponse""".split()
)

_DDL_PATTERN = re.compile(
    r"\b(create\s+table|alter\s+table|drop\s+table|create\s+index|drop\s+index)\b",
    re.IGNORECASE,
)

_TYPE_DECLARATION_RE = re.compile(
    r"^\s*(?:export\s+)?type\s+([A-Za-z0-9_]+)\s*=\s*(.*?)^(?=\s*(?:export\s+)?(?:type|interface)\b|\Z)",
    re.MULTILINE | re.DOTALL,
)

_INTERFACE_DECLARATION_RE = re.compile(
    r"^\s*(?:export\s+)?interface\s+([A-Za-z0-9_]+)\s*\{(.*?)^(?=\s*(?:export\s+)?(?:type|interface)\b|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _is_workspace_module(rel_path: str) -> bool:
    return any(
        rel_path == prefix or rel_path.startswith(prefix) for prefix in _WORKSPACE_MODULE_PREFIXES
    )


def _is_legacy_video_import(module: str) -> bool:
    if module in _LEGACY_VIDEO_EXACT:
        return True
    return any(module.startswith(prefix) for prefix in _LEGACY_VIDEO_MODULE_PREFIXES)


def _is_direct_executor_import(module: str) -> bool:
    return any(module.startswith(prefix) for prefix in _DIRECT_EXECUTOR_MODULE_PREFIXES)


def _source_files(root: Path, *globs: str) -> list[Path]:
    paths: list[Path] = []
    for pattern in globs:
        paths.extend(root.glob(pattern))
    return sorted(paths)


def check_workspace_legacy_video_imports(root: Path) -> list[str]:
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
        modules = imported_modules(tree)
        for module, lineno in modules.items():
            if _is_legacy_video_import(module):
                if (rel_path, module) in _LEGACY_VIDEO_EXCEPTIONS:
                    continue
                errors.append(
                    f"{rel_path}:{lineno}: legacy video phase/service import {module!r} "
                    "in generic Workspace module"
                )
    return errors


def check_job_execution_direct_executor_calls(root: Path) -> list[str]:
    errors: list[str] = []
    for path in _source_files(root, "server/app/**/*.py"):
        rel_path = path.relative_to(root).as_posix()
        if not rel_path.startswith(_JOB_SERVICE_PREFIX):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel_path)
        except SyntaxError as exc:
            errors.append(f"{rel_path}: syntax error ({exc})")
            continue
        modules = imported_modules(tree)
        for module, lineno in modules.items():
            if _is_direct_executor_import(module):
                errors.append(
                    f"{rel_path}:{lineno}: direct Executor invocation/import {module!r} "
                    "in job execution service; claim through leases instead"
                )
    return errors


def _body_is_handwritten(body: str) -> bool:
    cleaned = re.sub(r"\s+", " ", body).strip()
    if "ApiSchemas[" in cleaned or "components['schemas']" in cleaned:
        return False
    # References to other generated-derived aliases are acceptable.
    return "{" in cleaned


def check_frontend_handwritten_job_transports(root: Path) -> list[str]:
    errors: list[str] = []
    frontend_paths = set(root.glob("frontend/src/**/*.ts"))
    frontend_paths.update(root.glob("frontend/src/**/*.tsx"))
    for path in sorted(frontend_paths):
        rel_path = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in _TYPE_DECLARATION_RE.finditer(source):
            name = match.group(1)
            body = match.group(2)
            if name in _GENERATED_JOB_TRANSPORT_NAMES and _body_is_handwritten(body):
                errors.append(
                    f"{rel_path}:{source[: match.start()].count(chr(10)) + 1}: "
                    f"handwritten transport type {name!r} must be derived from generated "
                    "OpenAPI types (ApiSchemas['...'])"
                )
        for match in _INTERFACE_DECLARATION_RE.finditer(source):
            name = match.group(1)
            if name in _GENERATED_JOB_TRANSPORT_NAMES:
                errors.append(
                    f"{rel_path}:{source[: match.start()].count(chr(10)) + 1}: "
                    f"handwritten transport interface {name!r} must be derived from generated "
                    "OpenAPI types (ApiSchemas['...'])"
                )
    return errors


def check_jobs_route_include_router(root: Path) -> list[str]:
    """routes/jobs.py must not aggregate routers; compose them in routes/__init__.py."""
    rel_path = "server/app/routes/jobs.py"
    path = root / rel_path
    if not path.exists():
        return []
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError as exc:
        return [f"{rel_path}: syntax error ({exc})"]
    errors: list[str] = []
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        name = ast.unparse(call.func)
        if name.endswith("include_router"):
            errors.append(
                f"{rel_path}: include_router forbidden; "
                "compose focused routers in routes/__init__.py"
            )
    return errors


def check_job_deletion_service_is_singular(root: Path) -> list[str]:
    """Job deletion must be owned by JobDeletionService, not JobRerunService."""
    errors: list[str] = []
    routes_path = root / "server/app/routes/jobs.py"
    if routes_path.exists():
        source = routes_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(routes_path.relative_to(root)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = ast.unparse(node.func)
            if call_name in ("job_rerun.delete", "job_rerun.batch_delete"):
                errors.append(
                    f"server/app/routes/jobs.py:{node.lineno}: "
                    "job deletion must use JobDeletionService, not JobRerunService"
                )

    service_path = root / "server/app/services/job_rerun.py"
    if service_path.exists():
        source = service_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(service_path.relative_to(root)))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in ("delete", "batch_delete"):
                errors.append(
                    f"server/app/services/job_rerun.py:{node.lineno}: "
                    f"{node.name} belongs in JobDeletionService"
                )
    return errors


def check_schema_mutation_locations(root: Path) -> list[str]:
    errors: list[str] = []
    allowed_prefix = "server/app/db/migrations/"
    allowed_exact = "server/app/db/schema.py"
    for path in _source_files(root, "server/app/**/*.py"):
        rel_path = path.relative_to(root).as_posix()
        if rel_path == allowed_exact or rel_path.startswith(allowed_prefix):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel_path)
        except SyntaxError as exc:
            errors.append(f"{rel_path}: syntax error ({exc})")
            continue
        reported: set[int] = set()
        for node in ast.walk(tree):
            value: str | None = None
            lineno: int | None = None
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                lineno = node.lineno
            if isinstance(node, ast.JoinedStr):
                # DDL is not expected inside f-strings; reconstructing is expensive.
                continue
            if value is None or lineno is None:
                continue
            if _DDL_PATTERN.search(value) and lineno not in reported:
                reported.add(lineno)
                errors.append(
                    f"{rel_path}:{lineno}: schema mutation belongs in "
                    "server/app/db/migrations/ or server/app/db/schema.py"
                )
    return errors
