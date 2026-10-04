/**
 * 「外部对接」接入信息卡的最小调用示例（#870）。
 *
 * 内容提炼自 docs/workspace-api-tokens.md（对接契约的单一事实源）。
 * 防漂移：tests/routes/test_api_access_card_contract.py 把这里每一处
 * `$API_BASE/api/…`（curl）与 `{API_BASE}/api/…`（Python）调用钉在端点
 * 清单 apiAccessEndpoints.json 上，清单再与文档权限面表、后端 api-scope
 * 白名单、OpenAPI 契约三方对账——示例改用清单外的端点或方法即红。
 *
 * 只放 shell 变量 `$VAR`，不要写 `${VAR}`：本文件是 JS 模板字面量，
 * `${` 会被当成插值。
 */

export type ApiAccessSnippetInput = {
  apiBase: string
  workspaceId: string
}

// 示例里的 token 一律是占位符：明文只在签发响应里出现一次，不回填进示例。
export const API_TOKEN_PLACEHOLDER = '<粘贴签发的 API Token>'

export function buildCurlExample({
  apiBase,
  workspaceId,
}: ApiAccessSnippetInput): string {
  return `API_BASE="${apiBase}"
WORKSPACE_ID="${workspaceId}"
API_TOKEN="${API_TOKEN_PLACEHOLDER}"

# 1) 提交条目：一个 run，每项一个 job。items 还支持 material / bundle / ref，
#    见 docs/workspace-api-tokens.md
curl -sS -X POST "$API_BASE/api/workspaces/$WORKSPACE_ID/runs" \\
  -H "Authorization: Bearer $API_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"items": [{"type": "text", "content": "hello", "filename": "input.md"}]}'
# → {"run": {"id": "…"}, "created_count": 1, "job_ids": ["…"]}
# 重复提交同一条目返回 400 "No tasks were resolved from input"：表示已存在、
# 不是失败，对账见 docs/workspace-api-tokens.md「幂等与重试」

# 2) 轮询 job 状态，直到 completed / failed（建议间隔 10 秒以上）
JOB_ID="<上一步响应里的 job_ids 元素>"
curl -sS "$API_BASE/api/workspaces/$WORKSPACE_ID/jobs/$JOB_ID" \\
  -H "Authorization: Bearer $API_TOKEN"

# 3) 产物清单：object 条目带 download_url（直连）与 expires_at
curl -sS "$API_BASE/api/workspaces/$WORKSPACE_ID/jobs/$JOB_ID/artifacts" \\
  -H "Authorization: Bearer $API_TOKEN"

# 4) 下载：优先直连 download_url（不带 Authorization 头）；download_url 为
#    null、已过期或直连失败时才回落 raw 端点（-f：失败以非零退出，不把错误体
#    写成产物）。产物名按 URL 路径段 percent-encode（safe=""）：名字里的 # 或 ?
#    不编码会被当成 fragment / query 截断，服务端收到残缺名字返回 404
DOWNLOAD_URL="<清单条目的 download_url，为 null 时留空>"
ARTIFACT_NAME="<清单条目的 name>"
ENCODED_NAME=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$ARTIFACT_NAME")
if [ -z "$DOWNLOAD_URL" ] || ! curl -fsS --compressed -o out.bin "$DOWNLOAD_URL"; then
  curl -fsS --compressed -o out.bin "$API_BASE/api/workspaces/$WORKSPACE_ID/jobs/$JOB_ID/artifacts/$ENCODED_NAME/raw" \\
    -H "Authorization: Bearer $API_TOKEN"
fi
`
}

export function buildPythonExample({
  apiBase,
  workspaceId,
}: ApiAccessSnippetInput): string {
  return `import time
from urllib.parse import quote

import requests

API_BASE = "${apiBase}"
WORKSPACE_ID = "${workspaceId}"
API_TOKEN = "${API_TOKEN_PLACEHOLDER}"

ALREADY_EXISTS = "No tasks were resolved from input"  # 全部条目已有 job 的 400
# 条目级幂等键（#813）：换成外部系统自己的记录 id，重提时据此对账已有 job
CLIENT_TOKEN = "demo-1"

s = requests.Session()
s.headers["Authorization"] = f"Bearer {API_TOKEN}"

# 1) 提交条目。超时 / 5xx 后可原样重提，同一条目不会重复建 job；重提撞上
#    「上次其实成功了」时返回 400 ALREADY_EXISTS——是已存在、不是失败
item = {"type": "text", "content": "hello", "filename": "input.md", "client_token": CLIENT_TOKEN}
resp = s.post(f"{API_BASE}/api/workspaces/{WORKSPACE_ID}/runs", json={"items": [item]})
if resp.status_code == 400 and resp.json().get("detail") == ALREADY_EXISTS:
    job_ids = []  # 已存在：下面按去重键对账
else:
    resp.raise_for_status()
    submitted = resp.json()
    job_ids = submitted["job_ids"]
    if not job_ids:  # 可能为空（#501 治愈 / 并发重叠提交）：按 run 读回
        jobs = s.get(
            f"{API_BASE}/api/workspaces/{WORKSPACE_ID}/jobs",
            params={"run_id": submitted["run"]["id"]},
        ).json()["jobs"]
        job_ids = [job["id"] for job in jobs]
cursor = None
while not job_ids:
    # 按去重键对账：带 client_token 的 text 项，job 的 source_id 以 "~<token>"
    # 结尾。search 是子串匹配、按创建时间倒序分页：精确比对，沿 next_cursor 翻页
    page = s.get(
        f"{API_BASE}/api/workspaces/{WORKSPACE_ID}/jobs/snapshot",
        params={"search": f"~{CLIENT_TOKEN}", "limit": 500, "cursor": cursor},
    ).json()
    job_ids = [job["id"] for job in page["jobs"] if job["source_type"] == "material"
               and job["source_id"].endswith(f"~{CLIENT_TOKEN}")]
    cursor = page["next_cursor"]
    if cursor is None:
        break
if not job_ids:
    # 翻完也没有：该条目在本 workspace 没有 job（期间被删除等），按未提交处理
    raise SystemExit(f"{CLIENT_TOKEN}: 没有已有 job，按未提交处理后重提")
job_id = job_ids[0]

# 2) 轮询到终态；429 时按 Retry-After 退避
while True:
    r = s.get(f"{API_BASE}/api/workspaces/{WORKSPACE_ID}/jobs/{job_id}")
    if r.status_code == 429:
        time.sleep(int(r.headers.get("Retry-After", "10")))
        continue
    r.raise_for_status()
    status = r.json()["status"]
    if status in ("completed", "failed"):
        break
    time.sleep(15)

# 3) 产物清单 + 下载：直连优先，download_url 为 null 时回落 raw
manifest = s.get(f"{API_BASE}/api/workspaces/{WORKSPACE_ID}/jobs/{job_id}/artifacts").json()
for artifact in manifest["artifacts"]:
    data = None
    if artifact.get("download_url"):
        direct = requests.get(artifact["download_url"])  # 直连不带 token
        data = direct.content if direct.ok else None
    if data is None:
        raw = s.get(
            f"{API_BASE}/api/workspaces/{WORKSPACE_ID}/jobs/{job_id}/artifacts/{quote(artifact['name'], safe='/')}/raw"
        )
        raw.raise_for_status()
        data = raw.content
    print(artifact["name"], len(data))
`
}
