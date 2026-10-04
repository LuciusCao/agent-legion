"""控制台「外部对接」接入信息卡 ↔ 对接契约对账（#870）。

接入信息卡把 docs/workspace-api-tokens.md 的端点清单、最小示例搬进了 UI。
文档才是对接契约的单一事实源：卡片内容一旦与文档 / 代码分叉，对接方照着
控制台抄就会撞 404 或解析不存在的字段——与 #736（文档示例无守卫、代码一变
就静默漂移）同一病根。本测试把卡片的两份数据源钉住，纯静态、不碰 DB：

- 端点清单 frontend/src/components/settings/apiAccessEndpoints.json 与文档
  「权限面」表的 (method, path) 全等，与后端 api-scope 准入面
  （auth/api_scope_surface.API_SCOPE_INTAKE_ROUTE_NAMES，经 OpenAPI 生成的
  frontend/src/generated/api.ts 反解路由名）全等——三方任何一方单边增删即红；
- 示例 frontend/src/components/settings/apiAccessSnippets.ts 里每一次 curl /
  requests 调用都落在端点清单内，且覆盖「提交 → 轮询 → 清单 → raw 下载」全链路；
- Python 示例解析的响应字段存在于清单端点的响应 schema 里；示例引用的
  「已存在」400 文本、job 终态集合、卡片展示的限流 env 名与代码一致；
- 文档里「在控制台哪里签发」的导航指引与设置页 section 标签一致。

与 tests/routes/test_external_integration_docs_contract.py（文档示例 ↔ 契约）
互补：那边守文档自身，这边守 UI 对文档的镜像。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/workspace-api-tokens.md"
API_TS = ROOT / "frontend/src/generated/api.ts"
SETTINGS = ROOT / "frontend/src/components/settings"
CATALOG = SETTINGS / "apiAccessEndpoints.json"
SNIPPETS = SETTINGS / "apiAccessSnippets.ts"
INFO_CARD = SETTINGS / "ApiAccessInfoCard.tsx"
SETTINGS_PAGE = ROOT / "frontend/src/pages/SettingsPage.tsx"

_TS_PATH = re.compile(r"^  '(/api/[^']+)': \{\n(.*?)^  \}", re.DOTALL | re.MULTILINE)
_TS_METHOD = re.compile(r"^    (get|post|put|delete|patch): operations\['(\w+)'\]", re.MULTILINE)
_TS_SCHEMA = re.compile(r"^    (\w+): \{\n(.*?)^    \}", re.DOTALL | re.MULTILINE)
_TS_FIELD = re.compile(r"^      (\w+)\??: (.*)$", re.MULTILINE)
_TS_SCHEMA_REF = re.compile(r"components\['schemas'\]\['(\w+)'\]")
_TS_RESPONSE = re.compile(
    r"\b2\d\d: \{\s*headers: \{[^}]*\}\s*content: \{\s*"
    r"'application/json': components\['schemas'\]\['(\w+)'\]"
)
_DOC_ENDPOINT = re.compile(r"`(GET|POST|PUT|PATCH|DELETE) (/[^`]*)`")
_CURL_LINE = re.compile(r"curl [^\n]*")
_CURL_URL = re.compile(r'"\$API_BASE(/api/[^"]*)"')
_CURL_METHOD = re.compile(r"-X\s+([A-Z]+)")
_PY_CALL = re.compile(r"\bs\.(get|post)\(\s*f\"\{API_BASE\}(/api/[^\"]*)\"")

Endpoint = tuple[str, str]


def _catalog() -> tuple[str, list[Endpoint]]:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    endpoints = [(e["method"], e["path"]) for e in data["endpoints"]]
    for entry in data["endpoints"]:
        assert entry["purpose"].strip(), f"{entry}: 缺作用说明"
    return data["prefix"], endpoints


def _doc_surface() -> set[Endpoint]:
    """文档「权限面」表第一列的 (method, path)：从 **权限面** 标记后的第一张
    表起、到表结束（首个非表格行）止。"""
    lines = DOC.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if "**权限面**" in line)
    rows: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("|"):
            rows.append(stripped)
        elif rows:
            break
    surface = {pair for row in rows for pair in _DOC_ENDPOINT.findall(row.split("|")[1])}
    assert surface, "文档权限面表未解析到任何端点——守卫失效"
    return surface


def _contract_paths() -> dict[str, dict[str, str]]:
    text = API_TS.read_text(encoding="utf-8").partition("export interface operations {")[0]
    return {
        template: {m.upper(): op for m, op in _TS_METHOD.findall(body)}
        for template, body in _TS_PATH.findall(text)
    }


def _route_name(operation_id: str, template: str, method: str) -> str:
    # FastAPI 默认 operation id = re.sub(r"\W", "_", 函数名 + path) + "_" + method。
    suffix = re.sub(r"\W", "_", template) + "_" + method.lower()
    assert operation_id.endswith(suffix), (operation_id, suffix)
    return operation_id[: -len(suffix)]


def _normalise(path: str) -> list[str]:
    """示例 URL → 段列表：`$VAR` / `{expr}`（含嵌套括号引号的 f-string 表达式）
    一律视作变量段 `{}`。"""
    path = re.sub(r"\{[^{}]*\}", "{}", path)
    return ["{}" if seg.startswith("$") else seg for seg in path.strip("/").split("/")]


def _matches(call: str, template: str) -> bool:
    got, want = _normalise(call), template.strip("/").split("/")
    return len(got) == len(want) and all(
        (g == "{}") == w.startswith("{") and (g == "{}" or g == w)
        for g, w in zip(got, want, strict=True)
    )


def _snippet_calls() -> dict[str, list[tuple[str, str]]]:
    source = SNIPPETS.read_text(encoding="utf-8")
    curl_body = source.split("export function buildCurlExample", 1)[1].split(
        "export function buildPythonExample", 1
    )[0]
    python_body = source.split("export function buildPythonExample", 1)[1]
    curl = []
    for line in _CURL_LINE.findall(curl_body):
        url = _CURL_URL.search(line)
        if url is None:
            continue  # 直连 download_url：不是 API 调用
        method = _CURL_METHOD.search(line)
        curl.append((method.group(1) if method else "GET", url.group(1)))
    python = [(m.upper(), path) for m, path in _PY_CALL.findall(python_body)]
    return {"curl": curl, "python": python}


def test_catalog_matches_the_doc_permission_table() -> None:
    prefix, endpoints = _catalog()
    assert len(set(endpoints)) == len(endpoints), "端点清单有重复项"
    assert set(endpoints) == _doc_surface(), (
        "apiAccessEndpoints.json 与 docs/workspace-api-tokens.md 权限面表不一致"
    )
    doc = DOC.read_text(encoding="utf-8")
    assert f"`{prefix}` 为前缀" in doc


def test_catalog_matches_the_backend_api_scope_surface() -> None:
    from server.app.auth.api_scope_surface import API_SCOPE_INTAKE_ROUTE_NAMES

    prefix, endpoints = _catalog()
    paths = _contract_paths()
    routes = set()
    for method, path in endpoints:
        template = prefix + path
        assert template in paths, f"{template} 不在 OpenAPI 契约里"
        operation = paths[template].get(method)
        assert operation is not None, f"{template} 没有 {method}"
        routes.add(_route_name(operation, template, method))
    assert routes == set(API_SCOPE_INTAKE_ROUTE_NAMES), (
        "端点清单与 api-scope 准入面不一致：",
        sorted(routes ^ set(API_SCOPE_INTAKE_ROUTE_NAMES)),
    )


def test_card_snippets_only_call_catalog_endpoints() -> None:
    prefix, endpoints = _catalog()
    templates = {(method, prefix + path) for method, path in endpoints}
    for lang, calls in _snippet_calls().items():
        assert calls, f"{lang} 示例未解析到任何调用——守卫失效"
        hit: set[str] = set()
        for method, call in calls:
            matched = [t for m, t in templates if m == method and _matches(call, t)]
            assert matched, f"{lang} 示例 {method} {call} 不在端点清单内"
            hit.update(matched)
        # 全链路：提交 → 轮询 → 产物清单 → raw 兜底下载，每一环都有示例。
        required = {
            prefix + "/runs",
            prefix + "/jobs/{job_id}",
            prefix + "/jobs/{job_id}/artifacts",
            prefix + "/jobs/{job_id}/artifacts/{artifact_name}/raw",
        }
        assert required <= hit, (lang, sorted(required - hit))
        assert ("POST", f"{prefix}/runs") in {
            (m, t) for m, call in calls for mm, t in templates if mm == m and _matches(call, t)
        }, f"{lang} 示例缺 POST /runs 提交"


def _reachable_fields() -> set[str]:
    """端点清单各 operation 的 2xx JSON 响应 schema 及其递归引用的全部字段名。"""
    text = API_TS.read_text(encoding="utf-8")
    components = text.split("export interface components {", 1)[1]
    components = components.split("export interface operations {", 1)[0]
    schemas = {name: body for name, body in _TS_SCHEMA.findall(components)}
    operations = text.split("export interface operations {", 1)[1]
    prefix, endpoints = _catalog()
    paths = _contract_paths()
    pending = []
    for method, path in endpoints:
        op = paths[prefix + path][method]
        start = re.search(rf"^  {op}: \{{$", operations, re.MULTILINE)
        assert start is not None, op
        body = operations[start.end() :]
        end = re.search(r"^  \}$", body, re.MULTILINE)
        response = _TS_RESPONSE.search(body[: end.start() if end else None])
        if response:
            pending.append(response.group(1))
    seen: set[str] = set()
    fields: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        for field, raw in _TS_FIELD.findall(schemas[name]):
            fields.add(field)
            pending.extend(_TS_SCHEMA_REF.findall(raw))
    return fields


def test_python_snippet_reads_only_documented_response_fields() -> None:
    body = SNIPPETS.read_text(encoding="utf-8").split("export function buildPythonExample", 1)[1]
    # 请求头 / 请求体（json={...}）不是响应字段访问。
    code = "\n".join(
        line for line in body.splitlines() if "headers" not in line and "json=" not in line
    )
    keys = set(re.findall(r"\[\"(\w+)\"\]", code)) | set(re.findall(r"\['(\w+)'\]", code))
    keys |= set(re.findall(r"\.get\(\"(\w+)\"\)", code))
    assert {"job_ids", "run", "id", "jobs", "status", "artifacts", "name", "download_url"} <= keys
    # #907 按去重键对账：snapshot 分页的精确比对字段。
    assert {"source_type", "source_id", "next_cursor"} <= keys
    # `detail` 是 FastAPI 错误体（「已存在」400），不在 2xx 响应 schema 里；
    # 它的取值由 test_snippet_and_card_facts_match_code 对账到 run_service。
    unknown = keys - _reachable_fields() - {"detail"}
    assert not unknown, f"Python 示例解析了响应 schema 里不存在的字段 {sorted(unknown)}"


def test_snippet_and_card_facts_match_code() -> None:
    from server.app.configuration.env_overrides import _ENV_OVERRIDES
    from server.app.executors._lease_control import TERMINAL_JOB_STATUSES

    snippets = SNIPPETS.read_text(encoding="utf-8")
    already_exists = "No tasks were resolved from input"
    assert already_exists in snippets
    assert already_exists in DOC.read_text(encoding="utf-8")
    service = (ROOT / "server/app/services/run_service.py").read_text(encoding="utf-8")
    assert f'InvalidOperationError("{already_exists}")' in service

    terminal = re.search(r"if status in \(([^)]*)\):", snippets)
    assert terminal is not None
    assert set(re.findall(r"\"(\w+)\"", terminal.group(1))) == set(TERMINAL_JOB_STATUSES)

    card = INFO_CARD.read_text(encoding="utf-8")
    env_names = set(re.findall(r"AGENT_LEGION_API_TOKEN_RATE_LIMIT_\w+", card))
    assert env_names == {
        "AGENT_LEGION_API_TOKEN_RATE_LIMIT_PER_MINUTE",
        "AGENT_LEGION_API_TOKEN_RATE_LIMIT_BURST",
    }
    for name in env_names:
        assert _ENV_OVERRIDES[name][0][0] == "auth", name


def test_curl_snippet_download_is_an_encoded_fallback() -> None:
    """#907：raw 只在直连缺失 / 失败的 then 分支里请求（直连成功不再白耗一次
    Host 与限流额度）；产物名段是 quote(..., safe="") 编码后的变量（# / ? 不
    编码会被截断成残缺名字 404）。与文档 §4 下载块同一写法。"""
    source = SNIPPETS.read_text(encoding="utf-8")
    curl = source.split("export function buildCurlExample", 1)[1].split(
        "export function buildPythonExample", 1
    )[0]
    lines = curl.splitlines()
    encoded = re.search(
        r"^(\w+)=\$\(python3 -c '[^']*urllib\.parse\.quote\(sys\.argv\[1\], safe=\"\"\)"
        r"[^']*' \"\$ARTIFACT_NAME\"\)$",
        curl,
        re.MULTILINE,
    )
    assert encoded is not None, "curl 示例缺产物名 percent-encode 变量"
    raw = [
        i for i, line in enumerate(lines) if line.lstrip().startswith("curl ") and "/raw" in line
    ]
    assert len(raw) == 1, raw
    assert f'/artifacts/${encoded.group(1)}/raw"' in lines[raw[0]]
    branch = re.compile(r'^if \[ -z "\$DOWNLOAD_URL" \] \|\| ! curl [^\n]*"\$DOWNLOAD_URL"; then$')
    opener = [i for i, line in enumerate(lines) if branch.match(line)]
    assert len(opener) == 1 and opener[0] < raw[0], "raw 回落不在「直连失败才回落」分支里"
    assert "fi" in (line.strip() for line in lines[raw[0] + 1 :])
    assert all(line.strip() != "fi" for line in lines[opener[0] + 1 : raw[0]])
    unconditional = [line for line in lines if line.startswith("curl ") and "$DOWNLOAD_URL" in line]
    assert not unconditional, unconditional


def test_python_snippet_reconciles_duplicate_submission() -> None:
    """#907：「已存在」400 在 raise_for_status 之前识别，并按条目的
    client_token 去重键（source_id 后缀 ~<token>，见文档「对账」）翻 snapshot
    取已有 job——注释承诺的对账真的会执行。"""
    body = SNIPPETS.read_text(encoding="utf-8").split("export function buildPythonExample", 1)[1]
    check = body.index(
        'if resp.status_code == 400 and resp.json().get("detail") == ALREADY_EXISTS:'
    )
    assert check < body.index("resp.raise_for_status()")
    assert 'ALREADY_EXISTS = "No tasks were resolved from input"' in body
    assert '"client_token": CLIENT_TOKEN' in body
    assert '/jobs/snapshot",' in body and '"search": f"~{CLIENT_TOKEN}"' in body
    assert 'job["source_type"] == "material"' in body
    assert 'job["source_id"].endswith(f"~{CLIENT_TOKEN}")' in body
    # #909 review：重提刚耗掉限流额度时 snapshot 可能 429——对账分页须像轮询
    # 一样按 Retry-After 退避重取，并在读响应字段前 raise_for_status。
    loop = body[body.index("while not job_ids:") : body.index('cursor = page["next_cursor"]')]
    steps = [
        "/jobs/snapshot",
        "if r.status_code == 429:",
        'time.sleep(int(r.headers.get("Retry-After"',
        "continue",
        "r.raise_for_status()",
        "page = r.json()",
        'page["jobs"]',
    ]
    positions = [loop.index(step) for step in steps]
    assert positions == sorted(positions), list(zip(steps, positions, strict=True))
    # run_id 读回失败（含 429）不解析错误体，落到上面的去重键对账。
    assert 'readback.json()["jobs"]] if readback.ok else []' in body
    doc = DOC.read_text(encoding="utf-8")
    assert "`GET /jobs/snapshot?search=~<client_token>`" in doc


def test_doc_console_pointer_names_the_settings_section() -> None:
    page = SETTINGS_PAGE.read_text(encoding="utf-8")
    label = re.search(r"\{ id: 'api-access', label: '([^']+)' \}", page)
    assert label is not None, "设置页缺 api-access section"
    doc = DOC.read_text(encoding="utf-8")
    assert f"workspace 设置 → {label.group(1)}" in doc
