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


def _protocol_imports(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    classes: dict[str, str] = {}
    modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in _PROTOCOL_RESPONSE_MODULES:
                for alias in node.names:
                    if alias.name in _PROTOCOL_RESPONSE_NAMES:
                        classes[alias.asname or alias.name] = alias.name
            elif module in {"fastapi", "starlette"}:
                for alias in node.names:
                    if alias.name == "responses":
                        modules[alias.asname or alias.name] = f"{module}.responses"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _PROTOCOL_RESPONSE_MODULES:
                    local_name = alias.asname or alias.name.split(".")[0]
                    modules[local_name] = alias.name if alias.asname else local_name
    return classes, modules


def _is_resolved_protocol_name(name: str, classes: dict[str, str], modules: dict[str, str]) -> bool:
    if name in classes:
        return True
    prefix, _, response_name = name.rpartition(".")
    if response_name not in _PROTOCOL_RESPONSE_NAMES:
        return False
    root, _, remainder = prefix.partition(".")
    module = modules.get(root)
    return bool(module and (not remainder or f"{module}.{remainder}" in _PROTOCOL_RESPONSE_MODULES))


def has_protocol_response_annotation(
    function: ast.FunctionDef | ast.AsyncFunctionDef, tree: ast.AST
) -> bool:
    if function.returns is None:
        return False
    members = _annotation_members(function.returns)
    if not members:
        return False
    classes, modules = _protocol_imports(tree)
    return all(_is_resolved_protocol_name(member, classes, modules) for member in members)


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
