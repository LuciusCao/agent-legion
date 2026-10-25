"""Registration-decorator classification for zero_reference_exports."""

from __future__ import annotations

import ast

_REGISTERING_DECORATOR_NAMES = frozenset(
    {"entrypoint", "fixture", "command", "group", "task", "shared_task", "register"}
)
_REGISTERING_DECORATOR_ATTRS = frozenset(
    {
        "route",
        "websocket",
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "options",
        "head",
        "trace",
        "on",
        "event",
        *_REGISTERING_DECORATOR_NAMES,
    }
)


def registers_definition(decorator: ast.expr) -> bool:
    if isinstance(decorator, ast.Call):
        decorator = decorator.func
    if isinstance(decorator, ast.Name):
        return decorator.id in _REGISTERING_DECORATOR_NAMES
    return isinstance(decorator, ast.Attribute) and decorator.attr in _REGISTERING_DECORATOR_ATTRS
