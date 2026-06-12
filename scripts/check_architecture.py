#!/usr/bin/env python3
import ast
import json
from collections import Counter
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


def has_named_response_model(decorator: ast.Call) -> bool:
    for keyword in decorator.keywords:
        if keyword.arg == "response_model":
            return isinstance(keyword.value, (ast.Name, ast.Attribute))
    return False


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
    matches = [
        (module, lineno)
        for module, lineno in modules.items()
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)
    ]
    shortest_by_line: dict[int, str] = {}
    for module, lineno in matches:
        current = shortest_by_line.get(lineno)
        if current is None or len(module) < len(current):
            shortest_by_line[lineno] = module
    return sorted((module, lineno) for lineno, module in shortest_by_line.items())


def is_scheduler_path(relative_path: str) -> bool:
    return (
        relative_path.endswith("/scheduler.py")
        or relative_path == "server/app/pipeline_worker_thread.py"
        or relative_path.startswith("server/app/executors/scheduling/")
    )


def _assignment_target(call: ast.Call, parent_map: dict[ast.AST, ast.AST]) -> str:
    parent = parent_map.get(call)
    if isinstance(parent, ast.Assign):
        return ", ".join(ast.unparse(target) for target in parent.targets)
    if isinstance(parent, ast.AnnAssign):
        return ast.unparse(parent.target)
    return "<unassigned>"


def check_repository(root: Path) -> list[str]:
    config = json.loads((root / "config/architecture-budgets.json").read_text(encoding="utf-8"))
    exemptions = set(config.get("route_exemptions", []))
    annotation_exemptions = set(config.get("route_annotation_exemptions", []))
    route_import_baselines = config.get("route_import_baselines", {})
    scheduler_import_baselines = config.get("scheduler_import_baselines", {})
    scheduler_threadpool_baselines = config.get("scheduler_threadpool_baselines", {})
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
            allowed_imports = set(scheduler_import_baselines.get(relative_path, []))
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

        if not relative_path.startswith("server/app/routes/"):
            continue

        allowed_imports = set(route_import_baselines.get(relative_path, []))
        for module, lineno in forbidden_imports(modules, ROUTE_FORBIDDEN):
            if module not in allowed_imports:
                errors.append(f"{relative_path}:{lineno}: route boundary forbids import {module}")

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


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = check_repository(root)
    for error in errors:
        print(f"ERROR: {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
