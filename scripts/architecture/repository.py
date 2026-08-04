import ast
from collections import Counter
from pathlib import Path

from scripts.architecture.budget_policy import BudgetConfigurationError, load_budget_policy
from scripts.architecture.configuration import check_configuration_ownership
from scripts.architecture.executor_contracts import (
    check_executor_contract_models,
    check_frontend_executor_types,
    check_settings_store_legacy_agents,
    check_workspace_save_outside_transaction,
)
from scripts.architecture.executor_decoupling import (
    check_forbidden_patterns,
    check_legacy_modules_absent,
    check_workflow_worker_capability_branching,
)
from scripts.architecture.exemptions import categorize_exemptions, load_exemptions
from scripts.architecture.file_budgets import check_file_budgets
from scripts.architecture.helpers import (
    ROUTE_FORBIDDEN,
    SCHEDULER_FORBIDDEN,
    _assignment_target,
    annotation_contains_any,
    forbidden_imports,
    has_named_response_model,
    imported_modules,
    is_scheduler_path,
    reads_futures_length,
    reads_raw_executors_config,
    route_operations,
    threadpool_dict_by_workspace,
)
from scripts.architecture.import_cycles import check_import_cycles
from scripts.architecture.route_contracts import has_protocol_response_annotation
from scripts.architecture.service_boundaries import check_service_import_boundaries
from scripts.architecture.sql_placeholders import check_sql_placeholders
from scripts.architecture.video_legacy import check_video_legacy
from scripts.architecture.workflow import check_workflow_definitions
from scripts.architecture.workspace_boundaries import (
    check_frontend_handwritten_job_transports,
    check_job_deletion_service_is_singular,
    check_job_execution_direct_executor_calls,
    check_jobs_route_include_router,
    check_route_dag_and_deletion,
    check_schema_mutation_locations,
    check_workspace_legacy_video_imports,
)


def check_repository(root: Path) -> list[str]:
    errors: list[str] = []
    exemptions = load_exemptions(root)
    (
        response_model_exemptions,
        annotation_exemptions,
        route_import_exempt_files,
        route_import_exempt_modules,
        scheduler_import_exempt_files,
        scheduler_import_exempt_modules,
        scheduler_threadpool_exempt_targets,
    ) = categorize_exemptions(exemptions)
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
                scheduler_import_exempt = relative_path in scheduler_import_exempt_files
                exempt_scheduler_modules = scheduler_import_exempt_modules.get(relative_path, set())
                if not scheduler_import_exempt:
                    for module, lineno in forbidden_imports(modules, SCHEDULER_FORBIDDEN):
                        if module in exempt_scheduler_modules:
                            continue
                        errors.append(
                            f"{relative_path}:{lineno}: scheduler boundary forbids import {module}"
                        )
                allowed_targets = scheduler_threadpool_exempt_targets.get(relative_path, set())
                observed_targets: Counter[str] = Counter()
                for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                    name = ast.unparse(call.func)
                    if not name.endswith("ThreadPoolExecutor"):
                        continue
                    target = _assignment_target(call, parent_map)
                    observed_targets[target] += 1
                    if observed_targets[target] > (1 if target in allowed_targets else 0):
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

            errors.extend(check_service_import_boundaries(relative_path, tree))

            if not relative_path.startswith("server/app/routes/"):
                continue

            route_import_exempt = relative_path in route_import_exempt_files
            exempt_route_modules = route_import_exempt_modules.get(relative_path, set())
            for module, lineno in forbidden_imports(modules, ROUTE_FORBIDDEN):
                if route_import_exempt or module in exempt_route_modules:
                    continue
                errors.append(f"{relative_path}:{lineno}: route boundary forbids import {module}")

            for function, decorator in route_operations(tree):
                key = f"{relative_path}:{function.name}"
                if (
                    key not in response_model_exemptions
                    and not has_named_response_model(decorator)
                    and not has_protocol_response_annotation(function, tree)
                ):
                    errors.append(
                        f"{relative_path}:{decorator.lineno}: route {function.name} "
                        "requires named response_model"
                    )
                if key not in annotation_exemptions and annotation_contains_any(function):
                    errors.append(
                        f"{relative_path}:{function.lineno}: route {function.name} "
                        "return annotation may not contain Any"
                    )

    errors.extend(check_workflow_definitions(root))
    errors.extend(check_executor_contract_models(root))
    errors.extend(check_settings_store_legacy_agents(root))
    errors.extend(check_workspace_save_outside_transaction(root))
    errors.extend(check_frontend_executor_types(root))
    errors.extend(check_legacy_modules_absent(root))
    errors.extend(check_forbidden_patterns(root))
    errors.extend(check_workflow_worker_capability_branching(root))
    errors.extend(check_workspace_legacy_video_imports(root))
    errors.extend(check_job_execution_direct_executor_calls(root))
    errors.extend(check_video_legacy(root))
    errors.extend(check_route_dag_and_deletion(root))
    errors.extend(check_frontend_handwritten_job_transports(root))
    errors.extend(check_job_deletion_service_is_singular(root))
    errors.extend(check_jobs_route_include_router(root))
    errors.extend(check_schema_mutation_locations(root))
    errors.extend(check_import_cycles(root))
    errors.extend(check_configuration_ownership(root))
    errors.extend(check_sql_placeholders(root))

    try:
        policy = load_budget_policy(root / "config/architecture/architecture-budget-policy.yaml")
    except BudgetConfigurationError as exc:
        errors.append(f"budget configuration: {exc}")
    else:
        errors.extend(check_file_budgets(root, policy, exemptions))

    return errors
