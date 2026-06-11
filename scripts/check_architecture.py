#!/usr/bin/env python3
import ast
import json
from pathlib import Path

HTTP_DECORATORS = {"get", "post", "put", "patch", "delete"}
SCHEDULER_FORBIDDEN = (
    "server.app.pipeline.openclaw",
    "server.app.pipelines.pi_runner",
    "pipeline.openclaw",
    "pipelines.pi_runner",
    "pi_runner",
    "server.app.pipelines.reading_analysis",
    "pipelines.reading_analysis",
    "subprocess",
)
ROUTE_FORBIDDEN = (
    "server.app.cms",
    "server.app.pipelines.artifacts",
    "server.app.pipeline.artifacts",
    "cms",
    "pipelines.artifacts",
    "pipeline.artifacts",
)


def imported_modules(tree: ast.AST) -> dict[str, int]:
    modules: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules[alias.name] = node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules[node.module] = node.lineno
            for alias in node.names:
                modules[f"{node.module}.{alias.name}"] = node.lineno
    return modules


def route_operations(
    tree: ast.AST,
) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.Call]]:
    operations = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if decorator.func.attr in HTTP_DECORATORS:
                operations.append((node, decorator))
    return operations


def has_response_model(decorator: ast.Call) -> bool:
    return any(keyword.arg == "response_model" for keyword in decorator.keywords)


def annotation_contains_any(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if node.returns is None:
        return False
    for child in ast.walk(node.returns):
        if isinstance(child, ast.Name) and child.id == "Any":
            return True
        if isinstance(child, ast.Attribute) and child.attr == "Any":
            return True
    return False


def forbidden_imports(modules: dict[str, int], prefixes: tuple[str, ...]) -> list[tuple[str, int]]:
    return sorted(
        (module, lineno)
        for module, lineno in modules.items()
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)
    )


def is_scheduler_path(relative_path: str) -> bool:
    return (
        relative_path.endswith("/scheduler.py")
        or relative_path == "server/app/pipeline_worker_thread.py"
        or relative_path.startswith("server/app/executors/scheduling/")
    )


def _is_legacy_executor_assignment(call: ast.Call, parent_map: dict[ast.AST, ast.AST]) -> bool:
    parent = parent_map.get(call)
    if isinstance(parent, ast.Assign):
        return any(
            isinstance(target, ast.Attribute)
            and target.attr in {"_local_executor", "_agent_executor"}
            for target in parent.targets
        )
    if isinstance(parent, ast.AnnAssign):
        return isinstance(parent.target, ast.Attribute) and parent.target.attr in {
            "_local_executor",
            "_agent_executor",
        }
    return False


def check_repository(root: Path) -> list[str]:
    config = json.loads((root / "config/architecture-budgets.json").read_text(encoding="utf-8"))
    exemptions = set(config.get("route_exemptions", []))
    annotation_exemptions = set(config.get("route_annotation_exemptions", []))
    route_import_exemptions = set(config.get("route_import_exemptions", []))
    scheduler_import_exemptions = set(config.get("scheduler_import_exemptions", []))
    scheduler_threadpool_exemptions = set(config.get("scheduler_threadpool_exemptions", []))
    errors: list[str] = []

    server_root = root / "server/app"
    if not server_root.exists():
        return errors

    for path in sorted(server_root.rglob("*.py")):
        relative_path = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative_path)
        modules = imported_modules(tree)
        parent_map = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }

        if is_scheduler_path(relative_path):
            if relative_path not in scheduler_import_exemptions:
                for module, lineno in forbidden_imports(modules, SCHEDULER_FORBIDDEN):
                    errors.append(
                        f"{relative_path}:{lineno}: scheduler boundary forbids import {module}"
                    )
            if relative_path not in scheduler_threadpool_exemptions:
                for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                    name = ast.unparse(call.func)
                    if name.endswith("ThreadPoolExecutor") and not _is_legacy_executor_assignment(
                        call, parent_map
                    ):
                        errors.append(
                            f"{relative_path}:{call.lineno}: scheduler boundary forbids "
                            "ThreadPoolExecutor construction"
                        )

        if not relative_path.startswith("server/app/routes/"):
            continue

        if relative_path not in route_import_exemptions:
            for module, lineno in forbidden_imports(modules, ROUTE_FORBIDDEN):
                errors.append(f"{relative_path}:{lineno}: route boundary forbids import {module}")

        for function, decorator in route_operations(tree):
            key = f"{relative_path}:{function.name}"
            if key not in exemptions and not has_response_model(decorator):
                errors.append(
                    f"{relative_path}:{decorator.lineno}: route {function.name} "
                    "requires response_model"
                )
            if key not in annotation_exemptions and annotation_contains_any(function):
                errors.append(
                    f"{relative_path}:{function.lineno}: route {function.name} "
                    "return annotation may not contain Any"
                )

    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = check_repository(root)
    for error in errors:
        print(f"ERROR: {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
