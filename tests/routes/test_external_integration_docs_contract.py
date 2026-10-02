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

_CODE_BLOCK = re.compile(r"```(bash|python)\n(.*?)```", re.DOTALL)
_CURL_URL = re.compile(r'"\$HOST(/api/[^"]*)"')
_CURL_METHOD = re.compile(r"-X\s+([A-Z]+)")
# requests 调用：s.get(f"{HOST}/api/...", params={...}) / s.post(...)
_PY_CALL = re.compile(
    r"\bs\.(get|post)\(\s*f\"\{HOST\}(/api/[^\"]*)\"(.*?)\)(?=\.|\s|$)", re.DOTALL
)
_PY_PARAMS = re.compile(r"params=\{([^}]*)\}")
_PY_PARAM_KEY = re.compile(r"\"(\w+)\"\s*:")

# 响应字段访问：连续的 ["key"] / ['key'] / [0] 下标链。
_SUBSCRIPTS = r"(?:\[(?:\"\w+\"|'\w+'|\d+)\])+"
_SUBSCRIPT_STEP = re.compile(r"\[(?:\"(\w+)\"|'(\w+)'|(\d+))\]")
_STDIN_CHAIN = re.compile(r"json\.load\(sys\.stdin\)(" + _SUBSCRIPTS + ")")
_SHELL_VAR_PIPE = re.compile(
    r"\"\$(\w+)\"\s*\|\s*python3 -c '[^']*?json\.load\(sys\.stdin\)(" + _SUBSCRIPTS + ")"
)
_PY_VAR_ACCESS = re.compile(r"\b(\w+)(" + _SUBSCRIPTS + ")")
_PY_FOR = re.compile(r"\bfor (\w+) in (\w+)(" + _SUBSCRIPTS + ")")
_PY_JSON_ASSIGN = re.compile(r"\b(\w+) = (\w+)\.json\(\)\s*$", re.MULTILINE)

_TS_SCHEMA = re.compile(r"^    (\w+): \{\n(.*?)^    \}", re.DOTALL | re.MULTILINE)
_TS_FIELD = re.compile(r"^      (\w+)\??: (.*)$", re.MULTILINE)
_TS_REF = re.compile(r"^components\['schemas'\]\['(\w+)'\](\[\])?")
_TS_RESPONSE = re.compile(
    r"\b2\d\d: \{\s*headers: \{[^}]*\}\s*content: \{\s*"
    r"'application/json': components\['schemas'\]\['(\w+)'\]"
)


def _code_blocks(text: str) -> list[tuple[str, str]]:
    """(语言, 代码) 列表；python 块剥掉 # 注释（注释里的字段名不算访问）。"""
    blocks = []
    for lang, code in _CODE_BLOCK.findall(text):
        if lang == "python":
            code = "\n".join(re.sub(r"(^|\s)#.*$", "", line) for line in code.splitlines())
        blocks.append((lang, code))
    return blocks


def _curl_commands(block: str) -> list[tuple[str, str]]:
    """(curl 前的同行前缀, 整条 curl 命令含 \\ 续行)。"""
    commands = []
    lines = block.splitlines()
    for index, line in enumerate(lines):
        position = line.find("curl ")
        if position < 0:
            continue
        command = [line[position:]]
        cursor = index
        while lines[cursor].rstrip().endswith("\\") and cursor + 1 < len(lines):
            cursor += 1
            command.append(lines[cursor])
        commands.append((line[:position], "\n".join(command)))
    return commands


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
        for _, block in _code_blocks(text):
            for _, call in _curl_commands(block):
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


# --- 响应字段访问对账 ---------------------------------------------------------
# 示例里每一处对响应 JSON 的下标访问（curl 管道里的 json.load(sys.stdin)[…]、
# requests 的 .json()[…] 及其绑定变量 / for 循环变量上的 […]）都从文档代码
# 块里提取出来，沿 api.ts 的响应 schema 逐级解析：字段不存在即红。类型记号：
# ("schema", 名) / ("array", 元素) / ("leaf", 原文)。

_Type = tuple[str, object]


def _schemas() -> dict[str, dict[str, str]]:
    text = API_TS.read_text(encoding="utf-8")
    components = text.split("export interface components {", 1)[1]
    components = components.split("export interface operations {", 1)[0]
    return {name: dict(_TS_FIELD.findall(body)) for name, body in _TS_SCHEMA.findall(components)}


def _parse_type(raw: str) -> _Type:
    ref = _TS_REF.match(raw.strip())
    if ref is not None:
        element: _Type = ("schema", ref.group(1))
        return ("array", element) if ref.group(2) else element
    if raw.strip().split(" | ")[0].endswith("[]"):
        return ("array", ("leaf", raw))
    return ("leaf", raw)


def _response_schema(operation_id: str) -> str | None:
    text = API_TS.read_text(encoding="utf-8")
    operations = text.split("export interface operations {", 1)[1]
    start = re.search(rf"^  {operation_id}: \{{$", operations, re.MULTILINE)
    assert start is not None, operation_id
    body = operations[start.end() :]
    end = re.search(r"^  \}$", body, re.MULTILINE)
    match = _TS_RESPONSE.search(body[: end.start() if end else None])
    return match.group(1) if match else None  # raw 下载等非 JSON 响应


def _walk(start: _Type, chain: str, schemas: dict[str, dict[str, str]], where: str) -> _Type:
    current = start
    for name_dq, name_sq, index in _SUBSCRIPT_STEP.findall(chain):
        key = name_dq or name_sq
        if index:
            assert current[0] == "array", f"{where}: [{index}] 用在了非数组 {current}"
            current = current[1]  # type: ignore[assignment]
            continue
        assert current[0] == "schema", f"{where}: [{key!r}] 用在了非对象 {current}"
        fields = schemas[str(current[1])]
        assert key in fields, f"{where}: 响应 schema {current[1]} 没有字段 {key!r}"
        current = _parse_type(fields[key])
    return current


def _element(current: _Type, where: str) -> _Type:
    assert current[0] == "array", f"{where}: for 循环遍历的不是数组 {current}"
    return current[1]  # type: ignore[return-value]


def _route_type(path: str, method: str, paths: dict[str, dict[str, str]]) -> _Type:
    template = _match_template(path, list(paths))
    assert template is not None, path
    schema = _response_schema(paths[template][method])
    return ("schema", schema) if schema else ("leaf", "non-JSON response")


def _doc_response_accesses() -> list[tuple[str, _Type, str]]:
    """(定位, 起点类型, 下标链) 全集——起点是某次调用的响应或其派生变量。"""
    paths, _ = _contract()
    schemas = _schemas()
    accesses: list[tuple[str, _Type, str]] = []
    for doc in DOCS:
        for lang, block in _code_blocks((ROOT / doc).read_text(encoding="utf-8")):
            if lang == "bash":
                shell_vars: dict[str, _Type] = {}
                for prefix, command in _curl_commands(block):
                    url = _CURL_URL.search(command)
                    if url is None:
                        continue
                    method = _CURL_METHOD.search(command)
                    route = _route_type(
                        url.group(1).partition("?")[0], method.group(1) if method else "GET", paths
                    )
                    chains = _STDIN_CHAIN.findall(command)
                    accesses += [(f"{doc}: curl {url.group(1)}", route, c) for c in chains]
                    assigned = re.search(r"(\w+)=\$\(\s*$", prefix)
                    if assigned and not chains:
                        shell_vars[assigned.group(1)] = route
                for var, chain in _SHELL_VAR_PIPE.findall(block):
                    assert var in shell_vars, f"{doc}: ${var} 不是某次 curl 的响应"
                    accesses.append((f"{doc}: ${var}", shell_vars[var], chain))
                continue
            # python：先收绑定（var → 类型 / 未 .json() 的响应对象），再解析访问。
            bound: dict[str, _Type] = {}
            responses: dict[str, _Type] = {}
            direct: list[tuple[str, _Type, str]] = []
            for match in _PY_CALL.finditer(block):
                route = _route_type(match.group(2), match.group(1).upper(), paths)
                after = block[match.end() :]
                line_prefix = block[: match.start()].rsplit("\n", 1)[-1]
                json_chain = re.match(r"\.json\(\)(" + _SUBSCRIPTS + ")?", after)
                where = f"{doc}: s.{match.group(1)}({match.group(2)})"
                assign = re.search(r"\b(\w+) = $", line_prefix)
                loop = re.search(r"\bfor (\w+) in\s*$", line_prefix)
                if json_chain is None:
                    if assign:
                        responses[assign.group(1)] = route
                    continue
                chain = json_chain.group(1) or ""
                result = _walk(route, chain, schemas, where)
                if loop:
                    bound[loop.group(1)] = _element(result, where)
                elif assign:
                    bound[assign.group(1)] = result
                if chain:
                    direct.append((where, route, chain))
            for var, source in _PY_JSON_ASSIGN.findall(block):
                if source in responses:
                    bound[var] = responses[source]
            for _ in range(3):  # for 循环绑定可能依赖其它绑定：迭代到不动点
                for var, source, chain in _PY_FOR.findall(block):
                    if source in bound:
                        where = f"{doc}: for {var} in {source}{chain}"
                        bound[var] = _element(_walk(bound[source], chain, schemas, where), where)
            accesses += direct
            for var, chain in _PY_VAR_ACCESS.findall(block):
                if var in bound:
                    accesses.append((f"{doc}: {var}{chain}", bound[var], chain))
    return accesses


def test_doc_examples_response_fields_match_contracts() -> None:
    schemas = _schemas()
    accesses = _doc_response_accesses()
    for where, start, chain in accesses:
        _walk(start, chain, schemas, where)
    # 防「解析器失配 → 零访问 → 恒绿」：链路关键字段必须真被提取到并校验。
    keys = {key for _, _, chain in accesses for key in re.findall(r"\w+", chain)}
    assert {"api_token", "run", "id", "job_ids", "status", "jobs", "next_cursor"} <= keys, keys
    assert {"source_type", "source_id", "artifacts", "name"} <= keys, keys


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
