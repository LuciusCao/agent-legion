"""外部对接文档示例 ↔ OpenAPI 契约对账（#736）。

#736 的病根：runbook §9 的示例长期照抄着一个对 api token 一律 403 的
提交端点（job-batches），解析的字段也早已不在响应里——文档示例没有任何
机器守卫，代码一变就静默漂移。本测试把两份外部对接文档
（docs/remote-execution-runbook.md §9、docs/workspace-api-tokens.md）里
curl / requests 示例调用的每个 (method, URL 形态) 钉住：

- 必须是 OpenAPI 契约里真实存在的 (path 模板, method)——契约取自
  frontend/src/generated/api.ts（由后端 OpenAPI 生成，「Generated API
  Contract」检查保证与 live OpenAPI 一致），因此本测试纯静态、不碰 DB；
- 用 api token 调的端点必须在 api-scope 准入面（auth/api_scope_surface.py
  的路由名清单）内——否则照抄示例会撞 404；签发 token 的管理端点反之；
- 示例里用到的 query 参数必须是该 operation 声明的参数；
- 示例解析的响应字段、「已存在」400 的 detail 文本、job 终态集合与代码
  一致。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
DOCS = ("docs/remote-execution-runbook.md", "docs/workspace-api-tokens.md")
API_TS = ROOT / "frontend/src/generated/api.ts"

_CODE_BLOCK = re.compile(r"```(?:bash|python)\n(.*?)```", re.DOTALL)
# curl 调用：从 `curl` 到下一处 `curl` 之前为一次调用（含续行与 -X/-d）。
_CURL_CALL = re.compile(r"curl\b(.*?)(?=\bcurl\b|\Z)", re.DOTALL)
_CURL_URL = re.compile(r'"\$HOST(/api/[^"]*)"')
_CURL_METHOD = re.compile(r"-X\s+([A-Z]+)")
# requests 调用：s.get(f"{HOST}/api/...", params={...}) / s.post(...)
_PY_CALL = re.compile(
    r"\bs\.(get|post)\(\s*f\"\{HOST\}(/api/[^\"]*)\"(.*?)\)(?:\.|\s|$)", re.DOTALL
)
_PY_PARAMS = re.compile(r"params=\{([^}]*)\}")
_PY_PARAM_KEY = re.compile(r"\"(\w+)\"\s*:")

_TS_PATH = re.compile(r"^  '(/api/[^']+)': \{\n(.*?)^  \}", re.DOTALL | re.MULTILINE)
_TS_METHOD = re.compile(r"^    (get|post|put|delete|patch): operations\['(\w+)'\]", re.MULTILINE)
_TS_OPERATION = re.compile(
    r"^  (\w+): \{\n    parameters: \{\n(.*?)^    \}", re.DOTALL | re.MULTILINE
)
_TS_QUERY = re.compile(r"query\?: \{\n(.*?)\n      \}", re.DOTALL)
_TS_QUERY_KEY = re.compile(r"^\s+(\w+)\??:", re.MULTILINE)


def _is_variable(segment: str) -> bool:
    return segment.startswith("$") or ("{" in segment and "}" in segment)


def _doc_calls() -> list[tuple[str, str, str, frozenset[str]]]:
    """(文档, METHOD, 路径, query 参数名) 全集。"""
    calls: list[tuple[str, str, str, frozenset[str]]] = []
    for doc in DOCS:
        text = (ROOT / doc).read_text(encoding="utf-8")
        for block in _CODE_BLOCK.findall(text):
            for call in _CURL_CALL.findall(block):
                url = _CURL_URL.search(call)
                if url is None:
                    continue
                method = _CURL_METHOD.search(call)
                path, _, query = url.group(1).partition("?")
                keys = frozenset(p.split("=", 1)[0] for p in query.split("&") if p)
                calls.append((doc, method.group(1) if method else "GET", path, keys))
            for method, path, rest in _PY_CALL.findall(block):
                params = _PY_PARAMS.search(rest)
                keys = frozenset(_PY_PARAM_KEY.findall(params.group(1))) if params else frozenset()
                calls.append((doc, method.upper(), path, keys))
    return calls


def _contract() -> tuple[dict[str, dict[str, str]], dict[str, frozenset[str]]]:
    """api.ts → ({path 模板: {METHOD: operation id}}, {operation id: query 参数名})。"""
    text = API_TS.read_text(encoding="utf-8")
    paths_part, _, operations_part = text.partition("export interface operations {")
    paths = {
        template: {m.upper(): op for m, op in _TS_METHOD.findall(body)}
        for template, body in _TS_PATH.findall(paths_part)
    }
    queries: dict[str, frozenset[str]] = {}
    for op, params in _TS_OPERATION.findall(operations_part):
        query = _TS_QUERY.search(params)
        queries[op] = frozenset(_TS_QUERY_KEY.findall(query.group(1))) if query else frozenset()
    return paths, queries


def _match_template(path: str, templates: list[str]) -> str | None:
    """文档路径 → 唯一最具体的契约模板：字面段必须逐字相等，文档里的变量段
    （$JOB_ID / {job_id} / {quote(...)}）只能对上模板占位符；多个模板都能对上
    时取字面段最多的（jobs/snapshot 优先于 jobs/{job_id}）。"""
    segments = path.strip("/").split("/")
    best: tuple[int, str] | None = None
    for template in templates:
        parts = template.strip("/").split("/")
        if len(parts) != len(segments):
            continue
        literal_hits = 0
        for doc_seg, tpl_seg in zip(segments, parts, strict=True):
            placeholder = tpl_seg.startswith("{")
            if _is_variable(doc_seg):
                if not placeholder:
                    break
            elif placeholder:
                continue
            elif doc_seg != tpl_seg:
                break
            else:
                literal_hits += 1
        else:
            if best is None or literal_hits > best[0]:
                best = (literal_hits, template)
    return best[1] if best else None


def _route_name(operation_id: str, template: str, method: str) -> str:
    # FastAPI 默认 operation id（generate_unique_id）=
    # re.sub(r"\W", "_", 端点函数名 + path) + "_" + method——按模板剥掉后缀。
    suffix = re.sub(r"\W", "_", template) + "_" + method.lower()
    assert operation_id.endswith(suffix), (operation_id, suffix)
    return operation_id[: -len(suffix)]


def test_doc_examples_exist_in_openapi_and_token_surface() -> None:
    from server.app.auth.api_scope_surface import API_SCOPE_INTAKE_ROUTE_NAMES

    paths, queries = _contract()
    calls = _doc_calls()
    # 两份文档都必须真有示例被解析到（正则失配 = 守卫失效，宁红勿绿）。
    for doc in DOCS:
        assert any(c[0] == doc for c in calls), f"{doc}: 未解析到任何示例调用"

    seen_routes: set[str] = set()
    for doc, method, path, query_keys in calls:
        template = _match_template(path, list(paths))
        assert template is not None, f"{doc}: {method} {path} 不在 OpenAPI 契约里"
        operation = paths[template].get(method)
        assert operation is not None, f"{doc}: {template} 没有 {method}"
        unknown = query_keys - queries.get(operation, frozenset())
        assert not unknown, f"{doc}: {method} {template} 不认识 query 参数 {sorted(unknown)}"
        route = _route_name(operation, template, method)
        seen_routes.add(route)
        if route == "create_api_token":
            # 签发是管理员动作：api token 不能签发 sibling 凭据。
            assert route not in API_SCOPE_INTAKE_ROUTE_NAMES
        else:
            assert route in API_SCOPE_INTAKE_ROUTE_NAMES, (
                f"{doc}: {method} {template}（{route}）不在 api token 准入面，照抄会被拒"
            )

    # 全链路：签发 → 提交 → 轮询 → 下载，每一环都要有示例。
    assert {
        "create_api_token",
        "create_run",
        "get_external_job_status",
        "list_external_artifacts",
        "get_external_artifact_raw",
    } <= seen_routes
    # #736 的原始漂移：提交面不得回到 job-batches。
    assert all("job-batches" not in c[2] for c in calls)


def test_doc_examples_response_fields_match_contracts() -> None:
    from server.app.routes.external_artifact_contracts import (
        ExternalArtifactEntry,
        ExternalArtifactListResponse,
        ExternalJobStatusResponse,
    )
    from server.app.routes.job_list_contracts import JobsPageResponse
    from server.app.routes.job_view_contracts import JobsResponse, JobSummaryResponse
    from server.app.routes.run_contracts import RunCreateResponse, RunRecord
    from server.app.routes.workspace_api_token_contracts import (
        WorkspaceApiTokenCreatedResponse,
    )

    expected = {
        WorkspaceApiTokenCreatedResponse: {"api_token", "token_id"},
        RunCreateResponse: {"run", "created_count", "job_ids"},
        RunRecord: {"id", "status", "created_count"},
        JobsResponse: {"jobs", "truncated"},
        JobsPageResponse: {"jobs", "next_cursor"},
        JobSummaryResponse: {"id", "source_type", "source_id", "status"},
        ExternalJobStatusResponse: {"status", "error_summary", "artifacts"},
        ExternalArtifactListResponse: {"artifacts"},
        ExternalArtifactEntry: {"name", "content_hash", "uploaded_at"},
    }
    for model, fields in expected.items():
        missing = fields - set(model.model_fields)
        assert not missing, f"{model.__name__} 缺文档引用的字段 {sorted(missing)}"


def test_doc_idempotency_and_terminal_status_facts_match_code() -> None:
    from server.app.executors._lease_control import TERMINAL_JOB_STATUSES

    service = (ROOT / "server/app/services/run_service.py").read_text(encoding="utf-8")
    already_exists = "No tasks were resolved from input"
    assert f'InvalidOperationError("{already_exists}")' in service
    for doc in DOCS:
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert already_exists in text, f"{doc}: 缺「已存在」400 的 detail 文本"

    runbook = (ROOT / "docs/remote-execution-runbook.md").read_text(encoding="utf-8")
    loop = re.search(r'case "\$STATUS" in ([\w|]+)\)', runbook)
    assert loop is not None
    assert set(loop.group(1).split("|")) == set(TERMINAL_JOB_STATUSES)
