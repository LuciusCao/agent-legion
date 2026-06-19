import ast


def target_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.List, ast.Tuple)):
        return set().union(*(target_names(element) for element in target.elts))
    if isinstance(target, ast.Starred):
        return target_names(target.value)
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
        return {target.value.id}
    return set()


def match_bound_names(pattern: ast.pattern) -> set[str]:
    names = {
        node.name
        for node in ast.walk(pattern)
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None
    }
    names.update(
        node.rest for node in ast.walk(pattern) if isinstance(node, ast.MatchMapping) and node.rest
    )
    return names


class _SameScopeBindingCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def _bind(self, target: ast.expr) -> None:
        self.names.update(target_names(target))

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(alias.asname or alias.name for alias in node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._bind(target)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._bind(node.target)
        if node.value:
            self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._bind(node.target)
        self.visit(node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._bind(node.target)
        self.visit(node.value)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._bind(target)

    def visit_For(self, node: ast.For) -> None:
        self._bind(node.target)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._bind(node.target)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars:
                self._bind(item.optional_vars)
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        for item in node.items:
            if item.optional_vars:
                self._bind(item.optional_vars)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        for case in node.cases:
            self.names.update(match_bound_names(case.pattern))
        self.generic_visit(node)


def bound_names(statements: list[ast.stmt]) -> set[str]:
    collector = _SameScopeBindingCollector()
    for statement in statements:
        collector.visit(statement)
    return collector.names


class _GlobalCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Global(self, node: ast.Global) -> None:
        self.names.update(node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def class_global_rebound_names(statements: list[ast.stmt]) -> set[str]:
    globals_collector = _GlobalCollector()
    for statement in statements:
        globals_collector.visit(statement)
    return globals_collector.names & bound_names(statements)
