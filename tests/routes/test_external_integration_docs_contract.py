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
- 示例解析的响应字段（从代码块提取实际下标访问）、「已存在」400 的
  detail 文本、job 终态集合、limit 越界语义与代码一致；
- 示例里每一处 `[0]` 都先判空（空 job_ids / 空列表是文档承认的响应）；
- 示例按代码块钉住（#857）：fence 配平，每份文档期望的代码块逐块解析到、
  各自有调用，全链路各环按块校验——单个块丢失或截断即红。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
DOCS = ("docs/remote-execution-runbook.md", "docs/workspace-api-tokens.md")
API_TS = ROOT / "frontend/src/generated/api.ts"

# fence 只认行首（允许缩进：列表项里的代码块）：开 fence 必须带语言标记，
# 闭 fence 必须是裸 ```。按行配对而不是跨行正则——#857：正则
# ```(bash|python)\n(.*?)``` 在丢了结尾 fence 时会跨块吞并，被吞的块
# 不再独立解析，后续校验对它零访问恒绿。
_FENCE = re.compile(r"^[ \t]*```(.*)$")
# 示例解析范围：runbook 只看 §9（其余章节是运维命令，不是对接示例）；
# workspace-api-tokens.md 全文就是对接契约。
_DOC_SCOPE = {
    "docs/remote-execution-runbook.md": "## 9. ",
    "docs/workspace-api-tokens.md": None,
}
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
_INLINE_PY = re.compile(r"python3 -c '([^']*)'")
_INLINE_BIND = re.compile(r"\b(\w+)\s*=\s*json\.load\(sys\.stdin\)(" + _SUBSCRIPTS + ")?")
_INLINE_FIRST = re.compile(r"\b(\w+)\s*=\s*(\w+)\[0\] if \2 else\b")
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


def _fenced_blocks(doc: str, text: str) -> list[tuple[int, str, str]]:
    """全文按行配对 fence → (开 fence 行号, 语言, 代码)。配不平即红：开 fence
    缺语言标记、闭 fence 带语言标记（= 上一块丢了结尾 fence）、文末未闭合。"""
    blocks: list[tuple[int, str, str]] = []
    opened: tuple[int, str] | None = None
    body: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        fence = _FENCE.match(line)
        if fence is None:
            if opened is not None:
                body.append(line)
            continue
        marker = fence.group(1).strip()
        if opened is None:
            assert marker, f"{doc}:{number}: 开 fence 缺语言标记"
            opened, body = (number, marker), []
            continue
        assert not marker, (
            f"{doc}:{number}: 第 {opened[0]} 行的 ```{opened[1]} 未闭合就遇到 ```{marker}"
        )
        blocks.append((opened[0], opened[1], "\n".join(body) + "\n"))
        opened = None
    assert opened is None, f"{doc}:{opened[0]}: ```{opened[1]} 到文末未闭合"
    return blocks


def _scope_lines(doc: str, text: str) -> range:
    """示例范围的行号区间：指定章节标题起、到下一个二级标题止；None = 全文。"""
    lines = text.splitlines()
    heading = _DOC_SCOPE[doc]
    if heading is None:
        return range(1, len(lines) + 1)
    starts = [n for n, line in enumerate(lines, 1) if line.startswith(heading)]
    assert len(starts) == 1, f"{doc}: 找不到唯一的 {heading!r} 章节"
    ends = [n for n, line in enumerate(lines, 1) if n > starts[0] and line.startswith("## ")]
    return range(starts[0], ends[0] if ends else len(lines) + 1)


def _doc_blocks(doc: str) -> list[tuple[int, str, str]]:
    """文档示例范围内的 bash/python 块 → (行号, 语言, 代码)；python 块剥掉
    # 注释（注释里的字段名不算访问）。"""
    text = (ROOT / doc).read_text(encoding="utf-8")
    scope = _scope_lines(doc, text)
    blocks = []
    for line, lang, code in _fenced_blocks(doc, text):
        if line not in scope or lang not in ("bash", "python"):
            continue
        if lang == "python":
            code = "\n".join(re.sub(r"(^|\s)#.*$", "", row) for row in code.splitlines())
        blocks.append((line, lang, code))
    return blocks


def _code_blocks(doc: str) -> list[tuple[str, str]]:
    return [(lang, code) for _, lang, code in _doc_blocks(doc)]


def _curl_commands(block: str) -> list[tuple[str, str, int, int]]:
    """(curl 前的同行前缀, 整条 curl 命令含 \\ 续行, 起止字符偏移)。"""
    commands = []
    lines = block.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    for index, line in enumerate(lines):
        position = line.find("curl ")
        if position < 0:
            continue
        cursor = index
        while lines[cursor].rstrip().endswith("\\") and cursor + 1 < len(lines):
            cursor += 1
        start, end = offsets[index] + position, offsets[cursor + 1]
        commands.append((line[:position], block[start:end], start, end))
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


def _block_calls(block: str) -> list[tuple[str, str, frozenset[str]]]:
    """单个代码块里的 (METHOD, 路径, query 参数名)。"""
    calls: list[tuple[str, str, frozenset[str]]] = []
    for _, call, _, _ in _curl_commands(block):
        url = _CURL_URL.search(call)
        if url is None:
            continue
        method = _CURL_METHOD.search(call)
        path, _, query = url.group(1).partition("?")
        keys = frozenset(p.split("=", 1)[0] for p in query.split("&") if p)
        calls.append((method.group(1) if method else "GET", path, keys))
    for method, path, rest in _PY_CALL.findall(block):
        params = _PY_PARAMS.search(rest)
        keys = frozenset(_PY_PARAM_KEY.findall(params.group(1))) if params else frozenset()
        calls.append((method.upper(), path, keys))
    return calls


def _doc_calls() -> list[tuple[str, str, str, frozenset[str]]]:
    """(文档:块行号, METHOD, 路径, query 参数名) 全集。"""
    return [
        (f"{doc}:{line}", method, path, keys)
        for doc in DOCS
        for line, _, block in _doc_blocks(doc)
        for method, path, keys in _block_calls(block)
    ]


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
    # 「示例存在」由 test_doc_example_blocks_match_manifest 按块钉住；这里
    # 逐个调用对 OpenAPI 契约与 api token 准入面。
    for where, method, path, query_keys in calls:
        template = _match_template(path, list(paths))
        assert template is not None, f"{where}: {method} {path} 不在 OpenAPI 契约里"
        operation = paths[template].get(method)
        assert operation is not None, f"{where}: {template} 没有 {method}"
        unknown = query_keys - queries.get(operation, frozenset())
        assert not unknown, f"{where}: {method} {template} 不认识 query 参数 {sorted(unknown)}"
        route = _route_name(operation, template, method)
        if route == "create_api_token":
            # 签发是管理员动作：api token 不能签发 sibling 凭据。
            assert route not in API_SCOPE_INTAKE_ROUTE_NAMES
        else:
            assert route in API_SCOPE_INTAKE_ROUTE_NAMES, (
                f"{where}: {method} {template}（{route}）不在 api token 准入面，照抄会被拒"
            )
    # #736 的原始漂移：提交面不得回到 job-batches。
    assert all("job-batches" not in c[2] for c in calls)


# --- 代码块清单（#857）--------------------------------------------------------
# 「示例存在」按块钉住而不是按文档统计：#846 解冲突时 runbook §9 Python 段
# 的结尾 fence 一度丢失，Python 段被吞进相邻文本不再独立解析，但同文档的
# bash 段仍有调用，按文档统计的守卫全绿。这里声明每份文档示例范围内应有的
# 代码块（按出现顺序：语言、说明、该块自身必须覆盖的路由），逐块断言
# 解析到且各自有调用；全链路五环按块分别校验，不跨块/跨文档取并集。

_FULL_CHAIN = frozenset(
    {
        "create_api_token",  # 签发
        "create_run",  # 提交
        "get_external_job_status",  # 轮询
        "list_external_artifacts",  # 清单
        "get_external_artifact_raw",  # 下载
    }
)
_EXPECTED_BLOCKS: dict[str, tuple[tuple[str, str, frozenset[str]], ...]] = {
    "docs/remote-execution-runbook.md": (
        ("bash", "§9 全链路 curl 示例", _FULL_CHAIN),
        # Python 段是 bash 段的「等价」续写：从已签发的 WORKSPACE_API_TOKEN
        # 起步（签发是管理员一次性动作，不在调用方的 requests 会话里），
        # 其余四环必须自带。
        ("python", "§9 全链路 requests 示例", _FULL_CHAIN - {"create_api_token"}),
    ),
    "docs/workspace-api-tokens.md": (
        ("bash", "最小示例 1. 签发", frozenset({"create_api_token"})),
        ("bash", "最小示例 2. 提交", frozenset({"create_run"})),
        ("bash", "最小示例 2. 提交（client_token，#813）", frozenset({"create_run"})),
        (
            "bash",
            "最小示例 3. 轮询",
            frozenset(
                {
                    "get_external_job_status",
                    "get_run",
                    "list_workspace_jobs",
                    "snapshot_workspace_jobs",
                }
            ),
        ),
        (
            "bash",
            "最小示例 4. 下载",
            frozenset({"list_external_artifacts", "get_external_artifact_raw"}),
        ),
    ),
}


def _block_routes(block: str, paths: dict[str, dict[str, str]]) -> list[str]:
    routes = []
    for method, path, _ in _block_calls(block):
        template = _match_template(path, list(paths))
        assert template is not None, f"{method} {path} 不在 OpenAPI 契约里"
        routes.append(_route_name(paths[template][method], template, method))
    return routes


def test_doc_fences_are_balanced() -> None:
    """fence 配平：``` 总数为偶数、每个开 fence 带语言标记、闭 fence 是裸
    ```——丢一个结尾 fence 会让后续块整体错位，必须当场红。"""
    for doc in DOCS:
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert text.count("```") % 2 == 0, f"{doc}: ``` 数量为奇数，有 fence 丢失"
        _fenced_blocks(doc, text)  # 行级配对，配不平即 AssertionError


def test_doc_example_blocks_match_manifest() -> None:
    assert set(_EXPECTED_BLOCKS) == set(DOCS) == set(_DOC_SCOPE)
    paths, _ = _contract()
    for doc, expected in _EXPECTED_BLOCKS.items():
        blocks = _doc_blocks(doc)
        assert [lang for _, lang, _ in blocks] == [lang for lang, _, _ in expected], (
            f"{doc}: 示例代码块与清单不符——解析到 "
            f"{[(line, lang) for line, lang, _ in blocks]}，期望 "
            f"{[(lang, label) for lang, label, _ in expected]}"
        )
        for (line, _, block), (_, label, required) in zip(blocks, expected, strict=True):
            routes = _block_routes(block, paths)
            assert routes, f"{doc}:{line}（{label}）: 未解析到任何调用"
            missing = required - set(routes)
            assert not missing, f"{doc}:{line}（{label}）: 缺少 {sorted(missing)} 的调用"


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


def _bash_accesses(
    doc: str, block: str, paths: dict[str, dict[str, str]], schemas: dict[str, dict[str, str]]
) -> list[tuple[str, _Type, str]]:
    """bash 块：每段 `python3 -c '…'` 读的 stdin 来自哪次 curl——管道在同一条
    curl 命令里、`< FILE`（FILE 是某次 curl 的 -o 目标）、或 `"$VAR" |`
    （VAR=$(curl …)）——据此确定起点类型，再提取段内的下标访问。"""
    spans: list[tuple[int, int, _Type]] = []
    files: dict[str, _Type] = {}
    shell_vars: dict[str, _Type] = {}
    for prefix, command, start, end in _curl_commands(block):
        url = _CURL_URL.search(command)
        if url is None:
            continue
        method = _CURL_METHOD.search(command)
        route = _route_type(
            url.group(1).partition("?")[0], method.group(1) if method else "GET", paths
        )
        spans.append((start, end, route))
        output = re.search(r"-o ([^\s)]+)", command)
        if output:
            files[output.group(1)] = route
        assigned = re.search(r"(\w+)=\$\(\s*$", prefix)
        if assigned and "python3 -c" not in command:
            shell_vars[assigned.group(1)] = route
    accesses: list[tuple[str, _Type, str]] = []
    for match in _INLINE_PY.finditer(block):
        snippet = match.group(1)
        if "json.load(sys.stdin)" not in snippet:
            continue
        where = f"{doc}: python3 -c '{snippet[:60]}…'"
        source = next((t for a, b, t in spans if a <= match.start() < b), None)
        redirect = re.match(r"\s*<\s*([^\s)]+)", block[match.end() :])
        piped = re.search(r"\"\$(\w+)\"\s*\|\s*$", block[: match.start()])
        if source is None and redirect:
            assert redirect.group(1) in files, f"{where}: {redirect.group(1)} 不是某次 curl 的 -o"
            source = files[redirect.group(1)]
        if source is None and piped:
            assert piped.group(1) in shell_vars, f"{where}: ${piped.group(1)} 不是 curl 响应"
            source = shell_vars[piped.group(1)]
        assert source is not None, f"{where}: 找不到 stdin 来自哪次 curl"
        accesses += [(where, source, chain) for chain in _STDIN_CHAIN.findall(snippet)]
        bound = {
            var: _walk(source, chain or "", schemas, where)
            for var, chain in _INLINE_BIND.findall(snippet)
        }
        # 判空取首元素的绑定：`e = a[0] if a else None` → e 是 a 的元素类型
        for var, source_var in _INLINE_FIRST.findall(snippet):
            if source_var in bound:
                bound[var] = _element(bound[source_var], where)
        for var, chain in _PY_VAR_ACCESS.findall(snippet):
            if var in bound:
                accesses.append((f"{where} {var}{chain}", bound[var], chain))
    return accesses


def _doc_response_accesses() -> list[tuple[str, _Type, str]]:
    """(定位, 起点类型, 下标链) 全集——起点是某次调用的响应或其派生变量。"""
    paths, _ = _contract()
    schemas = _schemas()
    accesses: list[tuple[str, _Type, str]] = []
    for doc in DOCS:
        for lang, block in _code_blocks(doc):
            if lang == "bash":
                accesses += _bash_accesses(doc, block, paths, schemas)
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
    # #739 直连下载：download_url / expires_at 须从 api.ts 的条目 schema 解析到
    assert {"download_url", "expires_at", "content_hash"} <= keys, keys


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


def _limit_bounds(router: Any, route_name: str) -> tuple[int | None, int | None]:
    """路由 limit 参数的 Query(ge/le) 约束（无约束 = 不做 422 校验）。"""
    for route in router.routes:
        if route.name != route_name:
            continue
        for param in route.dependant.query_params:
            if param.name == "limit":
                metadata = param.field_info.metadata
                ge = next((m.ge for m in metadata if hasattr(m, "ge")), None)
                le = next((m.le for m in metadata if hasattr(m, "le")), None)
                return ge, le
    raise AssertionError(f"{route_name}: 没有 limit 参数")


def test_doc_limit_validation_semantics_match_routes() -> None:
    """文档对 limit 越界 422 的说法与路由实际约束一致。

    契约约束（ge/le）不进 api.ts，这里直接构造路由（服务对象传 None：只读
    参数声明，不调用）读 FastAPI 的 Query 元数据；带 ge/le 的越界是 422。
    #852 起 snapshot 与 /runs、/jobs 同一约定（此前无约束、函数体内静默钳制）。"""
    from server.app.routes.job_list import create_job_list_router
    from server.app.routes.jobs import create_jobs_router
    from server.app.routes.runs import create_runs_router

    assert _limit_bounds(create_runs_router(None), "list_runs") == (1, 500)  # type: ignore[arg-type]
    assert _limit_bounds(create_jobs_router(None), "list_workspace_jobs") == (1, 2000)  # type: ignore[arg-type]
    assert _limit_bounds(create_job_list_router(None), "snapshot_workspace_jobs") == (1, 500)  # type: ignore[arg-type]

    tokens_doc = (ROOT / "docs/workspace-api-tokens.md").read_text(encoding="utf-8")
    assert "`limit` 默认 100、取值 1–500，越界 422" in tokens_doc
    assert "limit 默认 500、取值 1–2000" in tokens_doc
    assert "limit 默认 200、取值 1–500（越界 422）" in tokens_doc
    assert "钳" not in tokens_doc, "snapshot 已不钳制，文档不得残留钳制说法"
    row_422 = next(line for line in tokens_doc.splitlines() if line.startswith("| 422 |"))
    assert "`GET /runs` 与 `GET /jobs/snapshot` 的 `limit` 不在 1–500" in row_422
    assert "`GET /jobs` 的 `limit` 不在 1–2000" in row_422


def test_doc_cursor_validation_semantics_match_route() -> None:
    """#891：snapshot 的 cursor 解析失败在参数校验层 422（不落到 SQL 成 5xx，
    错误码表让调用方对 5xx 无限退避重试）；文档 422 行写明 cursor。行为面
    （各类坏 cursor → 422）由 tests/routes/test_job_list_filtering.py 钉住。"""
    from typing import get_type_hints

    from pydantic import AfterValidator

    from server.app.routes.job_list import create_job_list_router

    router = create_job_list_router(None)  # type: ignore[arg-type]
    route = next(r for r in router.routes if r.name == "snapshot_workspace_jobs")
    hint = get_type_hints(route.endpoint, include_extras=True)["cursor"]
    assert any(isinstance(m, AfterValidator) for m in getattr(hint, "__metadata__", ()))
    tokens_doc = (ROOT / "docs/workspace-api-tokens.md").read_text(encoding="utf-8")
    row_422 = next(line for line in tokens_doc.splitlines() if line.startswith("| 422 |"))
    assert "`GET /jobs/snapshot` 的 `cursor` 无法解析" in row_422


def test_doc_examples_guard_first_element_access() -> None:
    """示例里每一处 `[0]` 都必须先判空（#736 复审 P2）：job_ids 在 #501 治愈与
    并发重叠提交时是空数组、jobs/artifacts 列表也可以为空，直接下标照抄即
    IndexError。合法形态只有两种：`X[0] if X else …`（同一行），或此前已有
    `if not X` 分支；链式响应访问直接接 `[0]`（如 `…["job_ids"][0]`）一律拒绝。"""
    found = 0
    for doc in DOCS:
        for _, block in _code_blocks(doc):
            for match in re.finditer(r"\[0\]", block):
                found += 1
                line = (
                    block[: match.end()].rsplit("\n", 1)[-1]
                    + block[match.end() :].split("\n", 1)[0]
                )
                owner = re.search(r"(\w+)$", block[: match.start()])
                where = f"{doc}: {line.strip()}"
                assert (
                    owner is not None
                    and block[match.start() - len(owner.group(1)) - 1] not in "])."
                ), f"{where}: 对响应链直接取 [0]，须先绑定变量并判空"
                name = owner.group(1)
                inline = re.search(rf"\b{name}\[0\].*\bif {name} else\b", line)
                earlier = re.search(rf"\bif not {name}\b", block[: match.start()])
                assert inline or earlier, f"{where}: 取 {name}[0] 前没有判空"
    assert found, "未解析到任何 [0] 访问——守卫失效"


_RAW_ENCODED_NAME = re.compile(
    r"^\s*(\w+)=\$\(python3 -c '[^']*urllib\.parse\.quote\(sys\.argv\[1\], safe=\"\"\)[^']*'",
    re.MULTILINE,
)
_DIRECT_BRANCH = re.compile(r"^\s*if \[ -z \"\$\w+\" \] \|\| ! curl [^\n]*; then\s*$")


def test_doc_raw_downloads_are_encoded_fallbacks() -> None:
    """#907：raw 下载必须 (1) 只在直连失败的 then 分支里执行——直连成功不再
    请求 raw，不白耗 Host 与 token 限流额度；(2) 产物名段用 quote(..., safe="")
    预先编码的变量——清单名里的 # / ? 不编码会被截断成残缺名字 404。"""
    found = 0
    for doc in DOCS:
        for _, block in _code_blocks(doc):
            encoded = set(_RAW_ENCODED_NAME.findall(block))
            lines = block.splitlines()
            for _, command, start, _ in _curl_commands(block):
                url = _CURL_URL.search(command)
                if url is None or not url.group(1).endswith("/raw"):
                    continue
                found += 1
                where = f"{doc}: {command.strip()[:80]}"
                segment = url.group(1).split("/artifacts/", 1)[1].rsplit("/raw", 1)[0]
                assert segment.startswith("$") and segment[1:] in encoded, (
                    f'{where}: 产物名段 {segment} 不是 quote(..., safe="") 编码后的变量'
                )
                row = block[:start].count("\n")
                opener = next(
                    (i for i in range(row - 1, -1, -1) if _DIRECT_BRANCH.match(lines[i])), None
                )
                assert opener is not None, f"{where}: raw 回落不在「直连失败才回落」分支里"
                assert all(lines[i].strip() != "fi" for i in range(opener + 1, row)), where
                assert any(line.strip() == "fi" for line in lines[row + 1 :]), where
    assert found >= 2, "未解析到 raw 下载调用——守卫失效"


def test_runbook_reconciliation_rejects_ambiguous_matches() -> None:
    """#910：runbook §9 的 find_existing_job 翻完全部页收齐命中，多于一个即
    报错而不是返回第一个；text 项带 client_token 时按 job 的 client_token
    字段精确比对（不自己拆 source_id 后缀）。"""
    blocks = [code for _, lang, code in _doc_blocks("docs/remote-execution-runbook.md")]
    [block] = [code for code in blocks if "def find_existing_job(" in code]
    body = block[block.index("def find_existing_job(") : block.index("\nitems = ")]
    assert "client_token: str | None = None" in body
    assert 'j["client_token"] == client_token' in body
    assert "endswith(" not in body
    # 循环内只累积、不提前 return；唯一出口是翻完。
    loop = body[body.index("while True:") : body.index("if len(hits) > 1:")]
    assert "hits += [" in loop and "return" not in loop
    verdict = body[body.index("if len(hits) > 1:") :]
    assert verdict.index("raise ") < verdict.index("return hits[0] if hits else None")
