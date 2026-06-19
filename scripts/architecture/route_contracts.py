import ast
import types
from collections.abc import Callable
from typing import Any, get_args, get_type_hints

from starlette.responses import (
    FileResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)

_PROTOCOL_RESPONSE_CLASSES = (
    FileResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
_PROTOCOL_RESPONSE_NAMES = {response.__name__ for response in _PROTOCOL_RESPONSE_CLASSES}
_PROTOCOL_RESPONSE_MODULES = {"fastapi.responses", "starlette.responses"}


def _dotted_name(annotation: ast.expr) -> str | None:
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        parent = _dotted_name(annotation.value)
        return f"{parent}.{annotation.attr}" if parent else None
    return None


def _annotation_members(annotation: ast.expr) -> list[str] | None:
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        left = _annotation_members(annotation.left)
        right = _annotation_members(annotation.right)
        if left is None or right is None:
            return None
        return [*left, *right]
    name = _dotted_name(annotation)
    return [name] if name else None


class _LocalBindingVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(alias.asname or alias.name for alias in node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass


def _function_local_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    visitor = _LocalBindingVisitor()
    for argument in [
        *function.args.posonlyargs,
        *function.args.args,
        *function.args.kwonlyargs,
    ]:
        visitor.names.add(argument.arg)
    if function.args.vararg:
        visitor.names.add(function.args.vararg.arg)
    if function.args.kwarg:
        visitor.names.add(function.args.kwarg.arg)
    for statement in function.body:
        visitor.visit(statement)
    return visitor.names


def _apply_binding(statement: ast.stmt, bindings: dict[str, str | None]) -> None:
    if isinstance(statement, ast.ImportFrom):
        module = statement.module or ""
        for alias in statement.names:
            local_name = alias.asname or alias.name
            bindings[local_name] = None
            if module in _PROTOCOL_RESPONSE_MODULES and alias.name in _PROTOCOL_RESPONSE_NAMES:
                bindings[local_name] = alias.name
            elif module in {"fastapi", "starlette"} and alias.name == "responses":
                bindings[local_name] = f"{module}.responses"
        return
    if isinstance(statement, ast.Import):
        for alias in statement.names:
            local_name = alias.asname or alias.name.split(".")[0]
            bindings[local_name] = None
            if alias.name in _PROTOCOL_RESPONSE_MODULES:
                bindings[local_name] = alias.name if alias.asname else local_name
        return
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        bindings[statement.name] = None
        return
    visitor = _LocalBindingVisitor()
    visitor.visit(statement)
    for name in visitor.names:
        bindings[name] = None


def _scope_path(
    scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef,
    target: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[ast.Module | ast.FunctionDef | ast.AsyncFunctionDef, int]]:
    for index, statement in enumerate(scope.body):
        if statement is target:
            return [(scope, index)]
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            node is target for node in ast.walk(statement)
        ):
            nested = _scope_path(statement, target)
            if nested:
                return [(scope, index), *nested]
    return []


def _protocol_bindings(
    tree: ast.Module, function: ast.FunctionDef | ast.AsyncFunctionDef
) -> dict[str, str | None]:
    bindings: dict[str, str | None] = {}
    postponed = any(
        isinstance(statement, ast.ImportFrom)
        and statement.module == "__future__"
        and any(alias.name == "annotations" for alias in statement.names)
        for statement in tree.body
    )
    for scope, boundary in _scope_path(tree, function):
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for name in _function_local_names(scope):
                bindings[name] = None
        limit = len(scope.body) if postponed and isinstance(scope, ast.Module) else boundary
        for statement in scope.body[:limit]:
            _apply_binding(statement, bindings)
    return bindings


def _is_resolved_protocol_name(name: str, bindings: dict[str, str | None]) -> bool:
    if "." not in name:
        return bindings.get(name) in _PROTOCOL_RESPONSE_NAMES
    prefix, _, response_name = name.rpartition(".")
    if response_name not in _PROTOCOL_RESPONSE_NAMES:
        return False
    root, _, remainder = prefix.partition(".")
    module = bindings.get(root)
    return bool(module and (not remainder or f"{module}.{remainder}" in _PROTOCOL_RESPONSE_MODULES))


def has_protocol_response_annotation(
    function: ast.FunctionDef | ast.AsyncFunctionDef, tree: ast.AST
) -> bool:
    if function.returns is None:
        return False
    members = _annotation_members(function.returns)
    if not members:
        return False
    if not isinstance(tree, ast.Module):
        return False
    bindings = _protocol_bindings(tree, function)
    return all(_is_resolved_protocol_name(member, bindings) for member in members)


def has_protocol_response_type(annotation: object) -> bool:
    members = get_args(annotation) if isinstance(annotation, types.UnionType) else (annotation,)
    return bool(members) and all(
        isinstance(member, type) and issubclass(member, _PROTOCOL_RESPONSE_CLASSES)
        for member in members
    )


def has_protocol_response_endpoint(endpoint: Callable[..., Any]) -> bool:
    try:
        annotation = get_type_hints(endpoint).get("return")
    except (NameError, TypeError):
        annotation = getattr(endpoint, "__annotations__", {}).get("return")
        if isinstance(annotation, str):
            return False
    return has_protocol_response_type(annotation)
