#!/usr/bin/env python3
"""Generate API Surface sections for architecture docs from AST analysis."""

import argparse
import ast
import re
import sys
from pathlib import Path

import yaml

AUTO_START = "<!-- AUTO-GENERATED: scripts/generate_architecture.py -->"
AUTO_END = "<!-- END AUTO-GENERATED -->"
TODO_PLACEHOLDER_PATTERN = re.compile(r"<!--\s*TODO:\s*.*?(?:AST|自动生成).*?-->", re.IGNORECASE)


def replace_section(content: str, new_section: str) -> str:
    """Replace content between AUTO-GENERATED markers, or replace TODO placeholder, or insert after heading."""
    if AUTO_START in content and AUTO_END in content:
        pattern = re.compile(
            re.escape(AUTO_START) + r".*?" + re.escape(AUTO_END),
            re.DOTALL,
        )
        return pattern.sub(
            f"{AUTO_START}\n\n{new_section.strip()}\n\n{AUTO_END}",
            content,
        )

    # Replace old TODO placeholder if present
    if TODO_PLACEHOLDER_PATTERN.search(content):
        return TODO_PLACEHOLDER_PATTERN.sub(
            f"{AUTO_START}\n\n{new_section.strip()}\n\n{AUTO_END}",
            content,
        )

    # Insert after ## API Surface / Interface heading
    lines = content.splitlines()
    insert_idx = -1
    for i, line in enumerate(lines):
        if line.strip().lower() in ("## api surface / interface", "## api surface/interface"):
            insert_idx = i + 1
            break

    if insert_idx < 0:
        raise ValueError("Could not find '## API Surface / Interface' heading in document")

    new_lines = (
        lines[:insert_idx]
        + ["", AUTO_START, "", new_section.strip(), "", AUTO_END]
        + lines[insert_idx:]
    )
    return "\n".join(new_lines) + "\n"


# ---------------------------------------------------------------------------
# Backend extraction
# ---------------------------------------------------------------------------


def _find_router_prefix(func_body: list[ast.stmt], router_name: str) -> str:
    """Search function body for router assignment with prefix."""
    for stmt in func_body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == router_name
                    and isinstance(stmt.value, ast.Call)
                ):
                    for kw in stmt.value.keywords:
                        if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                            return kw.value.value
    return ""


def extract_fastapi_routes(root: Path) -> str:
    """Extract FastAPI router definitions from server/app/routes/*.py."""
    routes_dir = root / "server" / "app" / "routes"
    if not routes_dir.exists():
        return "_No routes directory found._\n"

    rows: list[tuple[str, str, str, str]] = []
    for py_file in sorted(routes_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        # Module-level router prefixes
        module_prefixes: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        prefix = _find_router_prefix([node], target.id)
                        if prefix:
                            module_prefixes[target.id] = prefix

        # Find create_*_router functions and their nested routes
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("create_"):
                continue
            prefix = _find_router_prefix(node.body, "router")
            for child in ast.walk(node):
                if not isinstance(child, ast.FunctionDef) or child is node:
                    continue
                for dec in child.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                        method = dec.func.attr.upper()
                        path_arg = ""
                        if dec.args and isinstance(dec.args[0], ast.Constant):
                            path_arg = dec.args[0].value
                        if prefix and path_arg:
                            path_arg = prefix.rstrip("/") + "/" + path_arg.lstrip("/")
                        elif prefix and not path_arg:
                            path_arg = prefix
                        rows.append(
                            (method, path_arg, child.name, f"routes/{py_file.name}")
                        )

    if not rows:
        return "_No routes found._\n"

    lines = ["> 所有路由挂载在 `/api` 前缀下。\n"]
    lines += ["| 方法 | 路径 | 处理函数 | 文件 |", "|------|------|----------|------|"]
    for method, path, func, file in rows:
        lines.append(f"| {method} | `{path}` | `{func}` | {file} |")
    return "\n".join(lines) + "\n"


def extract_models(root: Path) -> str:
    """Extract Pydantic BaseModel and TypedDict definitions."""
    app_dir = root / "server" / "app"
    if not app_dir.exists():
        return "_No app directory found._\n"

    rows: list[tuple[str, str, str, str]] = []
    for py_file in sorted(app_dir.rglob("*.py")):
        if py_file.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            kind = ""
            for base in node.bases:
                if isinstance(base, ast.Name):
                    if base.id == "BaseModel":
                        kind = "BaseModel"
                    elif base.id == "TypedDict":
                        kind = "TypedDict"
                elif isinstance(base, ast.Attribute) and base.attr in (
                    "BaseModel",
                    "TypedDict",
                ):
                    kind = base.attr

            if not kind:
                continue

            fields: list[str] = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    field_name = item.target.id
                    field_type = ast.unparse(item.annotation) if item.annotation else "Any"
                    fields.append(f"{field_name}: {field_type}")

            rel_path = str(py_file.relative_to(app_dir.parent))
            rows.append(
                (node.name, kind, ", ".join(fields) or "—", rel_path)
            )

    if not rows:
        return "_No models found._\n"

    lines = ["| 模型 | 类型 | 字段 | 文件 |", "|------|------|------|------|"]
    for name, kind, fields, file in rows:
        # Truncate long field lists
        display_fields = fields if len(fields) < 80 else fields[:77] + "..."
        lines.append(f"| {name} | {kind} | {display_fields} | {file} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Frontend extraction
# ---------------------------------------------------------------------------


def extract_frontend_routes(root: Path) -> str:
    """Extract React Router routes from frontend/src/App.tsx."""
    app_tsx = root / "frontend" / "src" / "App.tsx"
    if not app_tsx.exists():
        return "_No App.tsx found._\n"

    content = app_tsx.read_text(encoding="utf-8")
    lines = ["| 路径 | 页面组件 |", "|------|----------|"]

    # Match <Route path="..." element={<Component />} />
    # or <Route index element={<Component />} />
    pattern = re.compile(
        r'<Route\s+(?:(path=["\']([^"\']+)["\'])|(index))\s+element=\{<([A-Za-z_][A-Za-z0-9_]*)'
    )

    found = False
    for m in pattern.finditer(content):
        path = m.group(2) if m.group(2) else "(index)"
        component = m.group(4)
        if component in ("Navigate",):
            continue
        lines.append(f"| `{path}` | {component} |")
        found = True

    if not found:
        return "_No routes found._\n"
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Pipeline extraction
# ---------------------------------------------------------------------------


def _extract_str_list(node: ast.AST) -> list[str]:
    """Extract list of string constants from an AST node."""
    if not isinstance(node, ast.List):
        return []
    result = []
    for elt in node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            result.append(elt.value)
    return result


def extract_pipeline_phases(root: Path) -> str:
    """Extract video pipeline phase sequences from pipeline/phases.py."""
    phases_file = root / "server" / "app" / "pipeline" / "phases.py"
    if not phases_file.exists():
        return "_No phases.py found._\n"

    try:
        tree = ast.parse(phases_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return "_Could not parse phases.py._\n"

    knowledge_phases: list[str] = []
    question_phases: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if target.id == "KNOWLEDGE_PHASES":
                        knowledge_phases = _extract_str_list(node.value)
                    elif target.id == "QUESTION_PHASES":
                        question_phases = _extract_str_list(node.value)

    lines = []
    if knowledge_phases:
        lines.append("**知识视频（8 阶段）：**")
        lines.append(" → ".join(f"`{p}`" for p in knowledge_phases))
    if question_phases:
        lines.append("**题目解析视频（6 阶段）：**")
        lines.append(" → ".join(f"`{p}`" for p in question_phases))

    return "\n".join(lines) + "\n" if lines else "_No phase sequences found._\n"


# ---------------------------------------------------------------------------
# Config extraction
# ---------------------------------------------------------------------------


def extract_config(root: Path) -> str:
    """Extract top-level config keys from config/pipeline.yaml."""
    config_file = root / "config" / "pipeline.yaml"
    if not config_file.exists():
        return "_No pipeline.yaml found._\n"

    try:
        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except Exception:
        return "_Could not parse pipeline.yaml._\n"

    if not isinstance(data, dict):
        return "_Invalid config format._\n"

    descriptions = {
        "asr": "ASR 提供商配置（whisper / SenseVoice）",
        "openclaw": "OpenClaw 命令模板与工作目录",
        "pipelines": "Agent Legion DAG 流水线开关",
    }

    lines = []
    for key in sorted(data.keys()):
        desc = descriptions.get(key, "")
        if desc:
            lines.append(f"- `{key}` — {desc}")
        else:
            lines.append(f"- `{key}`")
    return "\n".join(lines) + "\n" if lines else "_Empty config._\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def generate_backend(root: Path) -> str:
    return (
        "### REST API 路由\n\n"
        + extract_fastapi_routes(root)
        + "\n### 数据模型\n\n"
        + extract_models(root)
    )


def generate_frontend(root: Path) -> str:
    return "### 页面路由\n\n" + extract_frontend_routes(root)


def generate_pipeline(root: Path) -> str:
    return "### 视频流水线阶段\n\n" + extract_pipeline_phases(root)


def generate_deployment(root: Path) -> str:
    return "### 顶层配置项\n\n" + extract_config(root)


MODULES = {
    "backend": ("docs/architecture/backend.md", generate_backend),
    "frontend": ("docs/architecture/frontend.md", generate_frontend),
    "pipeline": ("docs/architecture/pipeline.md", generate_pipeline),
    "deployment": ("docs/architecture/deployment.md", generate_deployment),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate architecture doc sections")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root")
    parser.add_argument("--module", choices=list(MODULES.keys()), help="Only generate one module")
    args = parser.parse_args()

    root = args.root.resolve()
    modules = [args.module] if args.module else list(MODULES.keys())

    for name in modules:
        rel_path, generator = MODULES[name]
        doc_path = root / rel_path
        if not doc_path.exists():
            print(f"Warning: {doc_path} not found, skipping", file=sys.stderr)
            continue

        content = doc_path.read_text(encoding="utf-8")
        new_section = generator(root)
        new_content = replace_section(content, new_section)
        doc_path.write_text(new_content, encoding="utf-8")
        print(f"Updated {rel_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
