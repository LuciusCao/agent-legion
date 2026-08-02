"""Executor contract ratchets for Workspace Executor governance.

These checks keep executor response models typed, frontend executor types derived
from generated OpenAPI types, and workspace saves inside the aggregate transaction.
"""

import ast
import re
from pathlib import Path

EXECUTOR_CONTRACT_FILES = (
    "server/app/routes/executor_contracts.py",
    "server/app/routes/job_contracts.py",
)
EXECUTOR_FIELD_NAMES = frozenset(
    {
        "executor_allocations",
        "node_bindings",
        "node_limits",
        "executor_configuration",
        "allocations",
        "bindings",
        "migration_warnings",
        "executors",
    }
)


def _is_basemodel_class(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "BaseModel":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "BaseModel":
            return True
    return False


def _annotation_has_dict_of_any(node: ast.AST) -> bool:
    has_dict = False
    has_any = False
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id == "dict":
            has_dict = True
        if isinstance(child, ast.Attribute) and child.attr == "dict":
            has_dict = True
        if isinstance(child, ast.Name) and child.id == "Any":
            has_any = True
        if isinstance(child, ast.Attribute) and child.attr == "Any":
            has_any = True
    return has_dict and has_any


def check_executor_contract_models(root: Path) -> list[str]:
    errors: list[str] = []
    for relative_path in EXECUTOR_CONTRACT_FILES:
        path = root / relative_path
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        except SyntaxError as exc:
            errors.append(f"{relative_path}: syntax error ({exc})")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not _is_basemodel_class(node):
                continue
            for item in node.body:
                if not isinstance(item, ast.AnnAssign):
                    continue
                if not isinstance(item.target, ast.Name):
                    continue
                field_name = item.target.id
                if field_name not in EXECUTOR_FIELD_NAMES:
                    continue
                if item.annotation is not None and _annotation_has_dict_of_any(item.annotation):
                    errors.append(
                        f"{relative_path}:{item.lineno}: "
                        f"executor response field {field_name} must not be typed as dict[str, Any]"
                    )
    return errors


LEGACY_AGENT_APIS = (
    "getWorkspaceAgents",
    "setWorkspaceAgent",
    "assignAgent",
    "unassignAgent",
)


def check_settings_store_legacy_agents(root: Path) -> list[str]:
    path = root / "frontend/src/stores/settingStore.ts"
    if not path.exists():
        return []
    source = path.read_text(encoding="utf-8")
    pattern = re.compile(r"\b(" + "|".join(re.escape(name) for name in LEGACY_AGENT_APIS) + r")\b")
    if pattern.search(source):
        return [
            "frontend/src/stores/settingStore.ts: "
            "Settings store must not import or call legacy Agent assignment APIs "
            f"({', '.join(LEGACY_AGENT_APIS)})"
        ]
    return []


def check_workspace_save_outside_transaction(root: Path) -> list[str]:
    path = root / "server/app/services/workspace_configuration.py"
    if not path.exists():
        return []
    source = path.read_text(encoding="utf-8")
    if "replace_workspace_executor_configuration" in source:
        return [
            "server/app/services/workspace_configuration.py: "
            "Workspace save performs Executor replacement outside the aggregate "
            "transaction helper; use update_workspace_configuration instead of "
            "replace_workspace_executor_configuration"
        ]
    return []


GENERATED_EXECUTOR_TYPES = (
    "ExecutorDefinition",
    "ExecutorAllocation",
    "WorkspaceExecutorConfiguration",
    "ExecutorCatalogResponse",
)


def check_frontend_executor_types(root: Path) -> list[str]:
    path = root / "frontend/src/types.ts"
    if not path.exists():
        return []
    source = path.read_text(encoding="utf-8")
    errors: list[str] = []
    for type_name in GENERATED_EXECUTOR_TYPES:
        declaration = re.search(
            rf"^\s*(?:export\s+)?type\s+{re.escape(type_name)}\b\s*=\s*",
            source,
            re.MULTILINE,
        )
        if declaration is None:
            continue
        after = source[declaration.end() :]
        body_match = re.search(
            r".*?^(?=\s*(?:export\s+)?(?:type|interface)\b|\Z)",
            after,
            re.DOTALL | re.MULTILINE,
        )
        body = body_match.group(0) if body_match else after
        body = re.sub(r"\s+", " ", body)
        if "ApiSchemas[" in body and "]" in body:
            continue
        errors.append(
            f"frontend/src/types.ts: {type_name} must be derived from generated "
            "OpenAPI types (ApiSchemas['...']) instead of a handwritten duplicate"
        )
    return errors
