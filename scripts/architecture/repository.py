import ast
import json
from collections import Counter
from pathlib import Path

from scripts.architecture.helpers import (
    ROUTE_FORBIDDEN,
    SCHEDULER_FORBIDDEN,
    _assignment_target,
    annotation_contains_any,
    forbidden_imports,
    has_named_response_model,
    imported_modules,
    is_scheduler_path,
    is_service_path,
    reads_futures_length,
    reads_raw_executors_config,
    route_operations,
    threadpool_dict_by_workspace,
)
from scripts.architecture.phase4 import (
    check_executor_contract_models,
    check_frontend_executor_types,
    check_settings_store_legacy_agents,
    check_workspace_save_outside_transaction,
)
from scripts.architecture.phase5 import (
    check_forbidden_patterns,
    check_legacy_modules_absent,
)
from scripts.architecture.phase6 import (
    check_frontend_handwritten_job_transports,
    check_job_execution_direct_executor_calls,
    check_route_dag_and_deletion,
    check_schema_mutation_locations,
    check_workspace_video_hive_imports,
)
from scripts.architecture.pipeline import check_pipeline_definitions


def check_repository(root: Path) -> list[str]:
    config = json.loads((root / "config/architecture-budgets.json").read_text(encoding="utf-8"))
    exemptions = set(config.get("route_exemptions", []))
    annotation_exemptions = set(config.get("route_annotation_exemptions", []))
    route_import_baselines = config.get("route_import_baselines", {})
    scheduler_import_baselines = config.get("scheduler_import_baselines", {})
    scheduler_threadpool_baselines = config.get("scheduler_threadpool_baselines", {})
    errors: list[str] = []

    server_root = root / "server/app"

    if server_root.exists():
        for path in sorted(server_root.rglob("*.py")):
            relative_path = path.relative_to(root).as_posix()
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative_path)
            modules = imported_modules(tree)
            parent_map = {
                child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
            }
            if is_scheduler_path(relative_path):
                allowed_imports = set(scheduler_import_baselines.get(relative_path, []))
                for module in sorted(allowed_imports - modules.keys()):
                    errors.append(f"{relative_path}: unused scheduler import baseline {module}")
                for module, lineno in forbidden_imports(modules, SCHEDULER_FORBIDDEN):
                    if module not in allowed_imports:
                        errors.append(
                            f"{relative_path}:{lineno}: scheduler boundary forbids import {module}"
                        )
                allowed_targets = scheduler_threadpool_baselines.get(relative_path, {})
                observed_targets: Counter[str] = Counter()
                for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                    name = ast.unparse(call.func)
                    if not name.endswith("ThreadPoolExecutor"):
                        continue
                    target = _assignment_target(call, parent_map)
                    observed_targets[target] += 1
                    if observed_targets[target] > int(allowed_targets.get(target, 0)):
                        errors.append(
                            f"{relative_path}:{call.lineno}: scheduler boundary forbids "
                            f"ThreadPoolExecutor construction assigned to {target}"
                        )
                for lineno in threadpool_dict_by_workspace(tree, parent_map):
                    errors.append(
                        f"{relative_path}:{lineno}: scheduler boundary forbids "
                        "ThreadPoolExecutor construction keyed by workspace"
                    )
                for lineno in reads_futures_length(tree):
                    errors.append(
                        f"{relative_path}:{lineno}: scheduler must not use "
                        "_futures length for capacity decisions"
                    )
                if relative_path == "server/app/pipeline_worker_thread.py":
                    from scripts.architecture.helpers import accesses_runner_or_agent

                    for lineno in accesses_runner_or_agent(tree):
                        errors.append(
                            f"{relative_path}:{lineno}: "
                            "PipelineWorkerThread must branch on capability, not .runner or .agent"
                        )

            if (
                relative_path.startswith("server/app/executors/")
                and not relative_path.endswith("/__init__.py")
                and not relative_path.startswith("server/app/executors/scheduling/")
            ):
                for lineno in reads_raw_executors_config(tree):
                    errors.append(
                        f"{relative_path}:{lineno}: executor module must read typed "
                        "ExecutorConfig instead of raw settings.config['executors']"
                    )

            if is_service_path(relative_path):
                for module, lineno in modules.items():
                    if module == "fastapi" or module.startswith("fastapi."):
                        errors.append(
                            f"{relative_path}:{lineno}: service boundary forbids import {module}"
                        )

            if relative_path == "server/app/routes/jobs.py":
                for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                    name = ast.unparse(call.func)
                    if name.endswith("include_router"):
                        errors.append(
                            f"{relative_path}: include_router forbidden; "
                            "compose focused routers in routes/__init__.py"
                        )

            if not relative_path.startswith("server/app/routes/"):
                continue

            allowed_imports = set(route_import_baselines.get(relative_path, []))
            for module, lineno in forbidden_imports(modules, ROUTE_FORBIDDEN):
                if module not in allowed_imports:
                    errors.append(
                        f"{relative_path}:{lineno}: route boundary forbids import {module}"
                    )

            for function, decorator in route_operations(tree):
                key = f"{relative_path}:{function.name}"
                if key not in exemptions and not has_named_response_model(decorator):
                    errors.append(
                        f"{relative_path}:{decorator.lineno}: route {function.name} "
                        "requires named response_model"
                    )
                if key not in annotation_exemptions and annotation_contains_any(function):
                    errors.append(
                        f"{relative_path}:{function.lineno}: route {function.name} "
                        "return annotation may not contain Any"
                    )

    errors.extend(check_pipeline_definitions(root))
    errors.extend(check_executor_contract_models(root))
    errors.extend(check_settings_store_legacy_agents(root))
    errors.extend(check_workspace_save_outside_transaction(root))
    errors.extend(check_frontend_executor_types(root))
    errors.extend(check_legacy_modules_absent(root))
    errors.extend(check_forbidden_patterns(root))
    errors.extend(check_workspace_video_hive_imports(root))
    errors.extend(check_job_execution_direct_executor_calls(root))
    errors.extend(check_route_dag_and_deletion(root))
    errors.extend(check_frontend_handwritten_job_transports(root))
    errors.extend(check_schema_mutation_locations(root))

    file_budgets = config.get("files", {})
    for relative_path, budget in file_budgets.items():
        path = root / relative_path
        if not path.exists():
            errors.append(f"{relative_path}: budgeted file does not exist")
            continue
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > int(budget):
            errors.append(
                f"{relative_path}: {line_count} lines exceeds budget {budget}; "
                "split responsibilities before adding more code"
            )

    budgeted_paths = set(file_budgets)
    defaults = config.get("defaults", {})
    for dir_rel, budget in defaults.items():
        dir_path = root / dir_rel
        if not dir_path.is_dir():
            continue
        for path in sorted(dir_path.rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if rel in budgeted_paths:
                continue
            if path.name == "__init__.py" or path.name.startswith("test_"):
                continue
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            if line_count > int(budget):
                errors.append(
                    f"{rel}: {line_count} lines exceeds budget {budget}; "
                    "split responsibilities before adding more code"
                )

    return errors
