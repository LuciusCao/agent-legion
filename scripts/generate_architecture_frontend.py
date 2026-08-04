"""Frontend route extraction for scripts/generate_architecture.py."""

import re
from pathlib import Path


def extract_frontend_routes(root: Path) -> str:
    """Extract React Router routes from frontend/src/AppRoutes.tsx."""
    app_tsx = root / "frontend" / "src" / "AppRoutes.tsx"
    if not app_tsx.exists():
        return "_No AppRoutes.tsx found._\n"

    content = app_tsx.read_text(encoding="utf-8")
    lines = ["| 路径 | 页面组件 |", "|------|----------|"]

    route_re = re.compile(r"<Route\b(.*?)element=\{<([A-Za-z_][A-Za-z0-9_]*)", re.DOTALL)
    path_re = re.compile(r'path=["\']([^"\']+)["\']')
    index_re = re.compile(r"\sindex(?:\s|>|$)")

    stack: list[str] = []
    found = False

    for m in route_re.finditer(content):
        attrs = m.group(1)
        component = m.group(2)
        if component == "Navigate":
            continue

        tail = content[m.end() :]
        brace_idx = tail.find("}")
        after = tail[brace_idx + 1 :].lstrip() if brace_idx >= 0 else ""
        is_self_closing = after.startswith("/>")

        path_match = path_re.search(attrs)
        index_match = index_re.search(attrs)

        if path_match:
            path = path_match.group(1)
        elif index_match:
            path = "(index)"
        else:
            path = ""

        parent = stack[-1] if stack else ""
        if path.startswith("/"):
            full_path = path
        elif path == "(index)":
            full_path = parent if parent else "/"
        else:
            full_path = (parent.rstrip("/") + "/" + path.lstrip("/")) if parent else path

        lines.append(f"| `{full_path}` | {component} |")
        found = True

        if not is_self_closing:
            stack.append(full_path)

    if not found:
        return "_No routes found._\n"
    return "\n".join(lines) + "\n"
