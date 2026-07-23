import ast
from pathlib import Path

# ruff: noqa: SIM905

# Every route module is checked: a prefix allowlist would silently stop
# covering new routes (agent_workers.py grew deletion calls while unlisted).
_ROUTES_PREFIX = "server/app/routes/"
_DAG_TRAVERSAL_NAMES = frozenset(
    {"downstream_nodes", "ancestor_closure", "find_ready_nodes", "allowed_nodes"}
)
_FILESYSTEM_DELETION_IMPORTS = {
    "os": frozenset({"remove", "rmdir", "unlink"}),
    "shutil": frozenset({"move", "rmtree"}),
}


def _is_checked_route(rel_path: str) -> bool:
    return rel_path.startswith(_ROUTES_PREFIX)


def _source_files(root: Path, *globs: str) -> list[Path]:
    paths: list[Path] = []
    for pattern in globs:
        paths.extend(root.glob(pattern))
    return sorted(paths)


class _DeletionCallVisitor(ast.NodeVisitor):
    def __init__(self, origins: dict[str, str] | None = None) -> None:
        self.origins = dict(origins or {})
        self.calls: list[tuple[ast.Call, str]] = []

    def _origin(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return self.origins.get(node.id)
        if isinstance(node, ast.Call):
            if self._origin(node.func) == "path/class":
                return "path/instance"
            return None
        if not isinstance(node, ast.Attribute):
            return None
        base = self._origin(node.value)
        if base in {"path/class", "path/instance"} and node.attr in {"rmdir", "unlink"}:
            return f"delete/{node.attr}"
        if base == "module/pathlib" and node.attr == "Path":
            return "path/class"
        if base in {"module/os", "module/shutil"}:
            module = base.removeprefix("module/")
            if node.attr in _FILESYSTEM_DELETION_IMPORTS[module]:
                return f"delete/{node.attr}"
        return None

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in {*_FILESYSTEM_DELETION_IMPORTS, "pathlib"}:
                self.origins[alias.asname or alias.name] = f"module/{alias.name}"

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "pathlib":
            for alias in node.names:
                if alias.name == "Path":
                    self.origins[alias.asname or alias.name] = "path/class"
            return
        allowed = _FILESYSTEM_DELETION_IMPORTS.get(node.module or "", frozenset())
        for alias in node.names:
            if alias.name in allowed:
                self.origins[alias.asname or alias.name] = f"delete/{alias.name}"

    def _bind(self, target: ast.expr, value: ast.expr | None) -> None:
        if not isinstance(target, ast.Name):
            return
        origin = self._origin(value) if value is not None else None
        if origin is None:
            self.origins.pop(target.id, None)
        else:
            self.origins[target.id] = origin

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._bind(target, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
        self._bind(node.target, node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.origins.pop(node.name, None)
        child = type(self)(self.origins)
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            child.origins.pop(argument.arg, None)
        for argument in filter(None, (node.args.vararg, node.args.kwarg)):
            if isinstance(argument, ast.arg):
                child.origins.pop(argument.arg, None)
        for statement in node.body:
            child.visit(statement)
        self.calls.extend(child.calls)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:
        origin = self._origin(node.func)
        if origin and origin.startswith("delete/"):
            self.calls.append((node, origin.removeprefix("delete/")))
        self.generic_visit(node)


def check_route_dag_and_deletion(root: Path) -> list[str]:
    errors: list[str] = []
    for path in _source_files(root, "server/app/**/*.py"):
        rel_path = path.relative_to(root).as_posix()
        if not _is_checked_route(rel_path):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel_path)
        except SyntaxError as exc:
            errors.append(f"{rel_path}: syntax error ({exc})")
            continue
        deletion_visitor = _DeletionCallVisitor()
        deletion_visitor.visit(tree)
        for call_node, deletion_name in deletion_visitor.calls:
            errors.append(
                f"{rel_path}:{call_node.lineno}: filesystem deletion {deletion_name!r} belongs in "
                "services; routes must call orchestration services"
            )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = ast.unparse(node.func)
            base_name = call_name.split("(")[0].split(".")[-1]
            if base_name in _DAG_TRAVERSAL_NAMES:
                errors.append(
                    f"{rel_path}:{node.lineno}: DAG traversal {base_name!r} belongs in services; "
                    "routes must call orchestration services"
                )
    return errors
