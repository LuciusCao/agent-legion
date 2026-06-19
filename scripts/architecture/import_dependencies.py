import ast
from pathlib import Path

from scripts.architecture.import_guards import (
    bound_names,
    class_global_rebound_names,
    match_bound_names,
    target_names,
)


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

    def _unbind_target(self, target: ast.expr) -> None:
        for name in target_names(target):
            self._unbind(name)

    def _visit_branch(
        self, statements: list[ast.stmt], type_names: set[str], module_names: set[str]
    ) -> tuple[set[str], set[str]]:
        self.type_checking_names = set(type_names)
        self.typing_module_names = set(module_names)
        for statement in statements:
            self.visit(statement)
        return set(self.type_checking_names), set(self.typing_module_names)

    def _merge_states(self, states: list[tuple[set[str], set[str]]]) -> None:
        type_names, module_names = map(set, states[0])
        for branch_type_names, branch_module_names in states[1:]:
            type_names &= branch_type_names
            module_names &= branch_module_names
        self.type_checking_names = type_names
        self.typing_module_names = module_names

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
        saved_state = (set(self.type_checking_names), set(self.typing_module_names))
        for statement in node.body:
            self.visit(statement)
        self.type_checking_names, self.typing_module_names = saved_state
        self._unbind(node.name)
        for name in class_global_rebound_names(node.body):
            self._unbind(name)

    def visit_If(self, node: ast.If) -> None:
        if self._is_type_checking_guard(node.test):
            for statement in node.orelse:
                self.visit(statement)
            return
        self.visit(node.test)
        initial = (set(self.type_checking_names), set(self.typing_module_names))
        self._merge_states(
            [self._visit_branch(node.body, *initial), self._visit_branch(node.orelse, *initial)]
        )

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._unbind_target(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value:
            self.visit(node.value)
        self._unbind_target(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        self._unbind_target(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._unbind_target(node.target)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._unbind_target(target)

    def _visit_loop(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        initial = (set(self.type_checking_names), set(self.typing_module_names))
        for name in target_names(node.target) | bound_names(node.body):
            self._unbind(name)
        body_state = self._visit_branch(
            node.body, self.type_checking_names, self.typing_module_names
        )
        self._merge_states(
            [
                self._visit_branch(node.orelse, *initial),
                self._visit_branch(node.orelse, *body_state),
            ]
        )

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        initial = (set(self.type_checking_names), set(self.typing_module_names))
        loop_type_names, loop_module_names = map(set, initial)
        for name in bound_names(node.body):
            loop_type_names.discard(name)
            loop_module_names.discard(name)
        body_state = self._visit_branch(node.body, loop_type_names, loop_module_names)
        self._merge_states(
            [
                self._visit_branch(node.orelse, *initial),
                self._visit_branch(node.orelse, *body_state),
            ]
        )

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars:
                self._unbind_target(item.optional_vars)
        before_body = (set(self.type_checking_names), set(self.typing_module_names))
        normal_state = self._visit_branch(node.body, *before_body)
        early_type_names, early_module_names = map(set, before_body)
        for name in bound_names(node.body):
            early_type_names.discard(name)
            early_module_names.discard(name)
        self._merge_states([normal_state, (early_type_names, early_module_names)])

    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node)

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        initial = (set(self.type_checking_names), set(self.typing_module_names))
        normal_state = self._visit_branch(node.body, *initial)
        states = [self._visit_branch(node.orelse, *normal_state)]
        handler_type_names, handler_module_names = map(set, initial)
        for name in bound_names(node.body):
            handler_type_names.discard(name)
            handler_module_names.discard(name)
        for handler in node.handlers:
            self.type_checking_names = set(handler_type_names)
            self.typing_module_names = set(handler_module_names)
            self.visit(handler)
            states.append((set(self.type_checking_names), set(self.typing_module_names)))
        self._merge_states(states)
        for statement in node.finalbody:
            self.visit(statement)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._visit_try(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type:
            self.visit(node.type)
        if node.name:
            self._unbind(node.name)
        for statement in node.body:
            self.visit(statement)

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        initial = (set(self.type_checking_names), set(self.typing_module_names))
        states = [initial]
        for case in node.cases:
            self.type_checking_names, self.typing_module_names = map(set, initial)
            for name in match_bound_names(case.pattern):
                self._unbind(name)
            if case.guard:
                self.visit(case.guard)
            states.append(
                self._visit_branch(case.body, self.type_checking_names, self.typing_module_names)
            )
        self._merge_states(states)


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
