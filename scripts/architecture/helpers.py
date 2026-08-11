import ast

HTTP_DECORATORS = {"get", "post", "put", "patch", "delete"}
# Function-body imports count as dependencies in these packages (lazy imports
# hid the executors/workflows/pipeline tangle from the cycle checker).
# Dotted prefixes, matched with str.startswith.
FUNCTION_BODY_IMPORT_PACKAGES = (
    "server.app.executors.",
    "server.app.workflows.",
    "server.app.pipeline.",
)
SCHEDULER_FORBIDDEN = (
    "server.app.executors.openclaw_runner",
    "server.app.workflows.pi_runner",
    "server.app.workflows.skills",
    "server.app.workflows.question_comprehension_info",
    "server.app.workflows.question_content",
    "executors.openclaw_runner",
    "workflows.pi_runner",
    "workflows.skills",
    "workflows.question_comprehension_info",
    "workflows.question_content",
    "pi_runner",
    "openclaw",
    "skills",
    "question_comprehension_info",
    "question_content",
    "subprocess",
)
ROUTE_FORBIDDEN = (
    "workspace_libs.cms",
    "server.app.cms",
    "cms",
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
        or relative_path == "server/app/workflow_worker_thread.py"
        or relative_path == "server/app/workflow_worker/thread.py"
        or relative_path.startswith("server/app/executors/scheduling/")
    )


def is_service_path(relative_path: str) -> bool:
    return relative_path.startswith("server/app/services/")


def _assignment_target(call: ast.Call, parent_map: dict[ast.AST, ast.AST]) -> str:
    parent = parent_map.get(call)
    if isinstance(parent, ast.Assign):
        return ", ".join(ast.unparse(target) for target in parent.targets)
    if isinstance(parent, ast.AnnAssign):
        return ast.unparse(parent.target)
    return "<unassigned>"


def _is_workspace_subscript(node: ast.AST) -> bool:
    if not isinstance(node, ast.Subscript):
        return False
    index = node.slice
    return (isinstance(index, ast.Name) and index.id in {"workspace_id", "ws_id"}) or (
        isinstance(index, ast.Constant)
        and isinstance(index.value, str)
        and "workspace" in index.value
    )


def accesses_runner_or_agent(tree: ast.AST) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in {"runner", "agent"}
    ]


def reads_futures_length(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "len":
            continue
        if not node.args:
            continue
        arg_text = ast.unparse(node.args[0])
        if "_futures" in arg_text:
            lines.append(node.lineno)
    return lines


def reads_raw_executors_config(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        value_text = ast.unparse(node.value)
        if "config" not in value_text:
            continue
        try:
            key = ast.literal_eval(node.slice)
        except Exception:
            continue
        if key == "executors":
            lines.append(node.lineno)
    return lines


def threadpool_dict_by_workspace(tree: ast.AST, parent_map: dict[ast.AST, ast.AST]) -> list[int]:
    lines: list[int] = []
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        name = ast.unparse(call.func)
        if not name.endswith("ThreadPoolExecutor"):
            continue
        parent = parent_map.get(call)
        if not isinstance(parent, ast.Assign):
            continue
        for target in parent.targets:
            if _is_workspace_subscript(target):
                lines.append(call.lineno)
    return lines
