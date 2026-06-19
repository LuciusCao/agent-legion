import ast
from pathlib import Path


def _module_name(path: Path) -> str:
    return ".".join(path.with_suffix("").parts)


def _candidate_dependencies(module: str, candidate: str, known: set[str]) -> set[str]:
    result: set[str] = set()
    if candidate in known:
        result.add(candidate)
    elif f"{candidate}.__init__" in known:
        result.add(f"{candidate}.__init__")
    parts = candidate.split(".")
    for length in range(1, len(parts)):
        package = ".".join(parts[:length])
        initializer = f"{package}.__init__"
        importer_inside_package = module == initializer or module.startswith(f"{package}.")
        if initializer in known and not importer_inside_package:
            result.add(initializer)
    return result


class _ImportTimeImportVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: list[ast.Import | ast.ImportFrom] = []
        self.type_checking_names: set[str] = set()
        self.typing_module_names: set[str] = set()

    def _unbind(self, name: str) -> None:
        self.type_checking_names.discard(name)
        self.typing_module_names.discard(name)

    def _visit_arguments(self, arguments: ast.arguments) -> None:
        for argument in [
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        ]:
            if argument.annotation:
                self.visit(argument.annotation)
        for default in [*arguments.defaults, *arguments.kw_defaults]:
            if default:
                self.visit(default)

    def _is_type_checking_guard(self, test: ast.expr) -> bool:
        if isinstance(test, ast.Name):
            return test.id in self.type_checking_names
        return (
            isinstance(test, ast.Attribute)
            and test.attr == "TYPE_CHECKING"
            and isinstance(test.value, ast.Name)
            and test.value.id in self.typing_module_names
        )

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.append(node)
        for alias in node.names:
            local_name = alias.asname or alias.name.split(".")[0]
            self._unbind(local_name)
            if alias.name == "typing":
                self.typing_module_names.add(local_name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append(node)
        for alias in node.names:
            local_name = alias.asname or alias.name
            self._unbind(local_name)
            if node.module == "typing" and alias.name == "TYPE_CHECKING":
                self.type_checking_names.add(local_name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_arguments(node.args)
        if node.returns:
            self.visit(node.returns)
        self._unbind(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_arguments(node.args)
        if node.returns:
            self.visit(node.returns)
        self._unbind(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_arguments(node.args)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in [*node.decorator_list, *node.bases]:
            self.visit(expression)
        for keyword in node.keywords:
            self.visit(keyword.value)
        saved_type_checking = set(self.type_checking_names)
        saved_typing_modules = set(self.typing_module_names)
        for statement in node.body:
            self.visit(statement)
        self.type_checking_names = saved_type_checking
        self.typing_module_names = saved_typing_modules
        self._unbind(node.name)

    def visit_If(self, node: ast.If) -> None:
        if self._is_type_checking_guard(node.test):
            for statement in node.orelse:
                self.visit(statement)
            return
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._unbind(target.id)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value:
            self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self._unbind(node.target.id)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self._unbind(node.target.id)


def _dependencies(module: str, path: Path, known: set[str]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = module.rsplit(".", 1)[0]
    result: set[str] = set()
    visitor = _ImportTimeImportVisitor()
    visitor.visit(tree)
    for node in visitor.imports:
        candidates: list[str] = []
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                imported = ".".join(part for part in (base, node.module or "") if part)
            else:
                imported = node.module or ""
            if imported:
                candidates.append(imported)
                candidates.extend(f"{imported}.{alias.name}" for alias in node.names)
        for candidate in candidates:
            result.update(_candidate_dependencies(module, candidate, known))
    return result


def _strongly_connected_components(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def visit(module: str) -> None:
        nonlocal index
        indexes[module] = index
        lowlinks[module] = index
        index += 1
        stack.append(module)
        on_stack.add(module)

        for dependency in sorted(graph[module]):
            if dependency not in indexes:
                visit(dependency)
                lowlinks[module] = min(lowlinks[module], lowlinks[dependency])
            elif dependency in on_stack:
                lowlinks[module] = min(lowlinks[module], indexes[dependency])

        if lowlinks[module] != indexes[module]:
            return
        component: list[str] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == module:
                break
        if len(component) > 1:
            components.append(tuple(sorted(component)))

    for module in sorted(graph):
        if module not in indexes:
            visit(module)
    return components


def _render_component(component: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({name.removesuffix(".__init__") for name in component}))


def check_import_cycles(root: Path) -> list[str]:
    paths = sorted(root.glob("server/app/**/*.py"))
    modules = {_module_name(path.relative_to(root)): path for path in paths}
    known = set(modules)
    graph = {module: _dependencies(module, path, known) for module, path in sorted(modules.items())}
    rendered = sorted(
        component
        for component in (
            _render_component(component) for component in _strongly_connected_components(graph)
        )
        if len(component) > 1
    )
    return [f"import cycle: {' -> '.join(component)}" for component in rendered]
