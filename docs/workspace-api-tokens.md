# 外部系统提交条目：Workspace API Token（issue #626）

外部系统（CMS、表单后端、定时任务、其他 agent）可以凭 workspace 级
API token 免登录提交条目创建 job，无需人工登录控制台。token 是
machine-to-machine 凭据：绑定且仅绑定一个 workspace，权限是 editor 的
「提交条目 + 轮询状态 + 下载产物」最小集——除了 runs 提交面、只读查询
与产物读取外的一切端点（管理面、workflow 定义、studio-agent 工具面、
其它 effecting 操作）对它一律拒绝（workspace 路由 404，与不存在同形态；
部分 effecting / 管理 / 用户端点 403——两者都是终局拒绝）。

本文是对接契约（端点、请求/响应形态、幂等与重试、产物读取、错误码）；照抄
可跑的端到端脚本（curl 与 Python，签发 → 提交 → 轮询 → 下载）在
[「读取产物」](#读取产物清单直连下载与全链路示例)一节末尾。

控制台的「外部对接」section（#870）是本文的镜像：接入参数（workspace_id、
API base、当前生效的限流）、下文「权限面」表的端点清单与最小 curl / Python
示例。镜像由 `tests/routes/test_api_access_card_contract.py` 与本文、后端
api-scope 准入面对账，改权限面表须同步 UI 端点清单。

## 最小示例

1. **签发**（管理员在控制台：workspace 设置 → 外部对接 → 签发
   API Token；或直接调管理 API，201）。明文 token 只显示一次，立即保存：

   ```bash
   curl -X POST "$HOST/api/workspaces/$WORKSPACE_ID/api-tokens" \
     -H "Authorization: Bearer $ADMIN_SESSION" \
     -H "Content-Type: application/json" \
     -d '{"label": "cms-cron", "ttl_hours": 720}'
   # → {"token_id": "…", "api_token": "<token_id>.<secret>", "workspace_id": "…", "label": "cms-cron"}
   ```

   `ttl_hours` 可省略表示不过期（取值 1–8760）；`label` 至多 128 字符。

2. **提交条目**（一个 run，每项一个 job）：

   ```bash
   curl -X POST "$HOST/api/workspaces/$WORKSPACE_ID/runs" \
     -H "Authorization: Bearer $API_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"items": [{"type": "material", "material_id": "mat-1"}]}'
   # → {"run": {"id": "…", "status": "created", …}, "created_count": 1, "job_ids": ["…"]}
   ```

   `items` 至少一项，每项按 `type` 四选一（字段之外的键一律 422）：
   `{"type": "material", "material_id"}`、`{"type": "bundle", "bundle_id"}`、
   `{"type": "ref", "connection_key", "external_id", "params"?}`、
   `{"type": "text", "content", "filename"?}`（文本内联，服务端按内容
   sha256 落成 material；`filename` 须以 `.md` / `.txt` / `.json` 结尾，
   `.json` 落盘为 `application/json; charset=utf-8`）。
   material/bundle 必须已上传就绪——items 只引用已有素材，本通道不收文件。

   material / bundle / text 项可选带 `client_token`（#813，条目级幂等键，
   1–64 字符 `[A-Za-z0-9._-]`、首字符为字母或数字，非法 422）：同一份内容
   要作为多个独立 job 存在时，给每份一个不同的 token（如外部系统自己的
   记录 id）；同 token 重提幂等命中同一 job。不传即现行为（纯内容寻址）。
   **token 在 workspace 内须按内容版本唯一**（#910）：一个 token 只对应
   一份内容，内容改了（新版本）就换新 token。同一 token 复用于不同内容时
   服务端照常各建一个 job（去重身份是 `material_id~token`），但之后按 token
   对账会命中多个 job、无法判定哪个对应本次内容（见「对账」）。
   ref 项不收 `client_token`（422）——`external_id` 本身就是调用方控制的
   命名空间，要多份 job 用不同的 `external_id` 即可：

   ```bash
   curl -X POST "$HOST/api/workspaces/$WORKSPACE_ID/runs" \
     -H "Authorization: Bearer $API_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"items": [
           {"type": "text", "content": "{\"order\": 1}", "filename": "order.json", "client_token": "order-1001"},
           {"type": "text", "content": "{\"order\": 1}", "filename": "order.json", "client_token": "order-1002"}
         ]}'
   # → 同一份文本、同一个 material，两个独立 job；job id 以 "~order-1001" / "~order-1002" 结尾
   ```

   响应三个字段：`run`（run 记录，`run.id` 即 run_id；`run.created_count`
   是该 run 累计的 job 数）、`created_count`（等于 `len(job_ids)`）、
   `job_ids`（本次调用写入该 run 的 job id 列表，#735；不是 run 全量）。
   串行提交时它就是本次新建的 job；完全相同的请求并发时两次响应可能
   返回同一批 id（见下文「幂等与重试」），所以**按 job id 去重合并，
   不要把各次响应的 `created_count` 相加当新建总数**。响应刻意不带 job 行
   详情（#467 A4），状态一律轮询读取。

3. **轮询状态**（只读；workspace 调度暂停时提交照常排队，job 状态表达等待）：

   ```bash
   # 单个 job：轻量状态视图（status/outcome/进度/error_summary/产物名单）
   curl "$HOST/api/workspaces/$WORKSPACE_ID/jobs/$JOB_ID" \
     -H "Authorization: Bearer $API_TOKEN"
   # 整个 run：run 记录 + job_stats（total / by_status 聚合）
   curl "$HOST/api/workspaces/$WORKSPACE_ID/runs/$RUN_ID" \
     -H "Authorization: Bearer $API_TOKEN"
   # 按 run 列 job：run_id 过滤（#735）；limit 默认 500、取值 1–2000
   # （越界 422），truncated=true 表示结果被 limit 截断
   curl "$HOST/api/workspaces/$WORKSPACE_ID/jobs?run_id=$RUN_ID" \
     -H "Authorization: Bearer $API_TOKEN"
   # 大 run 分页：limit 默认 200、取值 1–500（越界 422），按 next_cursor
   # 循环到 null——是否翻完以 next_cursor 为准，不要按返回条数判断
   curl "$HOST/api/workspaces/$WORKSPACE_ID/jobs/snapshot?run_id=$RUN_ID&limit=500" \
     -H "Authorization: Bearer $API_TOKEN"
   ```

   job 的终态是 `completed` / `failed`；`queued` / `running` / `paused` /
   `awaiting_approval`（工作流里的人工审批门，等控制台审批）都是非终态，
   继续轮询。`GET /runs`（`limit` 默认 100、取值 1–500，越界 422；按创建时间倒序）列
   本 workspace 最近的 run。`/jobs` 与 `/jobs/snapshot` 的 `run_id` 是过滤
   参数而非资源寻址：不存在或属于别的 workspace 的 run_id 返回空列表，不是
   404（404 语义属于 `GET /runs/{run_id}`）。

4. **下载产物**（#631 的三端点；job id 取自第 2 步的 `job_ids`，或第 3 步
   按 `run_id` 过滤的 jobs 列表。`job_ids` 可能是空数组——#501 治愈或并发
   重叠提交，见「幂等与重试」——取第一个元素前先判空，为空时按 `run_id`
   读回，仍为空再按去重键对账）：

   ```bash
   # 产物清单：含 storage/content_hash/size_bytes/uploaded_at/media_type/
   # content_encoding，object 条目另带 download_url + expires_at（#739）；
   # job 未完成或没有产出时 artifacts 是空数组（不是 404）
   curl "$HOST/api/workspaces/$WORKSPACE_ID/jobs/$JOB_ID/artifacts" \
     -H "Authorization: Bearer $API_TOKEN"
   # 产物名按 URL 路径段 percent-encode（safe=""）：名字里的 # 或 ? 不编码
   # 会被当成 fragment / query 截断，服务端收到残缺名字返回 404
   ENCODED_NAME=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$ARTIFACT_NAME")
   # 优先直连：download_url 是 presigned 对象存储地址，不带 Authorization 头
   # （token 不要发给对象存储主机）；--compressed 解码 gzip 产物。
   # 只有 download_url 为空（null：local 条目 / 未配置对象存储）或直连失败
   # （已过 expires_at 的 403、网络外不可达等）时才回落 raw 字节下载——
   # 直连成功就不再请求 raw。仅成功解析并执行的单区间 Range 请求返回 206，
   # 其余情况（gzip 对象、后缀/多区间/起点越界等）可能以 200 返回全量，
   # 客户端两种都要接受。对象被 bucket lifecycle 回收时答 404：用 --fail，
   # 别把错误体存成产物
   if [ -z "$DOWNLOAD_URL" ] || ! curl -fsS --compressed -o "$OUT" "$DOWNLOAD_URL"; then
     curl -fsS --compressed -o "$OUT" "$HOST/api/workspaces/$WORKSPACE_ID/jobs/$JOB_ID/artifacts/$ENCODED_NAME/raw" \
       -H "Authorization: Bearer $API_TOKEN"
   fi
   ```

   直连与 raw 两条通道的语义对照（响应头、gzip、重跑、吊销、有效期）与
   照抄可跑、带上述边界处理的完整脚本见下文
   [「读取产物」](#读取产物清单直连下载与全链路示例)。

Bearer 通道不需要 CSRF header（非 ambient 凭据）。token 泄露时在设置面板
吊销，使用中的调用立即 401。

## 语义与边界

- **格式**：`{token_id}.{secret}`，与 worker register token 相同的存储
  约定——库内只存 sha256，明文只在签发响应出现一次。支持签发时指定
  `ttl_hours` 过期时间，支持随时吊销（软吊销，行保留审计）。
- **权限面**（权威常量在 `auth/api_scope_surface.py`，#734 起 tag 派生、
  含产物三端点；下列路径均以 `/api/workspaces/{workspace_id}` 为前缀）：

  | 端点 | 作用 |
  | --- | --- |
  | `POST /runs` | 提交（唯一 effecting 面） |
  | `GET /runs` · `GET /runs/{run_id}` | run 列表 / 单 run + job_stats |
  | `GET /jobs` | job 列表（`run_id` / `status` 过滤，`limit` ≤ 2000 + `truncated`） |
  | `GET /jobs/snapshot` | 分页 job 列表（`run_id` 等过滤，`limit` 1–500 + `next_cursor`） |
  | `GET /jobs/{job_id}` | 单 job 轻量状态 |
  | `GET /jobs/{job_id}/artifacts` | 产物清单 |
  | `GET /jobs/{job_id}/artifacts/{artifact_name}/raw` | 产物字节流 |

  后三个的读取语义见下文
  [「读取产物」](#读取产物清单直连下载与全链路示例)。跨
  workspace 访问与其它 workspace 路由一律 404（与不存在同一形态，不可
  枚举）；部分 effecting / 管理端点 403；token 不能签发新 token、不能改
  workflow 定义。
  旧的 `POST /job-batches` 提交面不对任何 scoped token 开放（403），外部
  系统只走 `POST /runs`。
- **审计**：经 API token 提交的 run 在服务端结构化日志里记录 token_id
  （不冒充任何用户身份）；列表展示 `last_used_at` 最近使用水位（每分钟
  至多刷新一次；吊销后的重试也刷新水位，但仅当调用方持有正确 secret——
  只知道 token_id 的错误凭据刷不动它）。
- **请求限流（#738）**：每个 token 一个独立令牌桶（按 token_id 计数，
  互不影响）；控制台 cookie 会话与 studio-agent scoped token 不受限。桶参数
  为实例级、env-only（`auth` 段）：`AGENT_LEGION_API_TOKEN_RATE_LIMIT_PER_MINUTE`
  （补充速率，默认 60）与 `AGENT_LEGION_API_TOKEN_RATE_LIMIT_BURST`（桶容量，
  默认 20）；非整数或小于 1 启动即失败。限流覆盖该 token 的**每个**已鉴权
  请求（含状态轮询与被 404 拒绝的越界探测），一个 HTTP 请求只扣一次；只有
  secret 校验通过后才扣——只知道 token_id 的错误凭据扣不动别人的额度。
  超限返回 `429` + `Retry-After`（秒，按补充速率向上取整），客户端应按
  `Retry-After` 退避后重试；轮询优先用 `/jobs/snapshot?run_id=…` 一次取整批
  状态，而不是逐 job 轮询。拒绝在服务端结构化日志记录 `token_id` /
  `workspace_id` / `retry_after`（每 token 每分钟至多一条，附带被合并的次数）。
- **计数存储与副本语义**：计数在进程内存，进程重启清零（分钟级窗口，
  可接受）。多副本 http 平面下每个副本各自计数（per-replica best-effort，
  与 `last_used_at` 同哲学），总吞吐上限约为单副本限额 × 副本数；该边界
  记入 #740 部署拓扑文档。计数器在 `auth/api_token_limits.py` 的
  `ApiTokenLimiter` 协议后面，需要精确全局限流时替换为共享存储实现即可。
- **不做的事**：per-token 单独配置限额、日配额（#856）；管理员面
  按标签检索。

## 幂等与重试

**去重键.** 一个条目在 workspace 内只建一次 job：去重键是 job 的
`(source_type, source_id)`——material 项为 `("material", material_id)`、
bundle 项为 `("bundle", bundle_id)`、ref 项为
`("ref", "<connection_key>:<external_id>")`、text 项按内容 sha256 复用同一
material，等同 material 项。去重跨 run 生效：之前任何 run 已为该条目建过
job，再次提交就不会新建（要重新执行同一条目走控制台的重跑，不是重提交）。

带 `client_token` 的 material / bundle / text 项，`source_id` 变为
`<material_id 或 bundle_id>~<client_token>`（job id 随之为
`<workspace>_<workflow>_<source_id>`），所以同内容不同 token 各成一个
job、同 token 重提命中同一 job；token 也随 items 进入 run 摘要，不同 token
的提交是不同的 run。job 的 `input`（material_id / bundle_id）不变，下游
执行与同内容无 token 的 job 完全一致。不带 token 的条目身份与此前逐字节
相同，已有 job id / run id 不漂移。

`GET /jobs` 与 `GET /jobs/snapshot` 的 job 条目带两个只读字段（#925，由服务端
从 `source_id` 解析）：`client_token`（带 token 提交的 material / bundle /
text 条目为该 token，否则为 null；ref 条目恒为 null）与 `source_base_id`
（去掉 token 后的 material_id / bundle_id，无 token 时等于 `source_id`）。
同一材料不同 token 的多个 job 共享同一 `source_base_id`。调用方按这两个字段
比对即可，不要自己拆 `source_id` 字符串。`GET /jobs/{job_id}` 轻量状态视图
不含 `source_id`，也不带这两个字段。

**重复提交的三种响应.**

| 情形 | 响应 | 调用方处理 |
| --- | --- | --- |
| 部分条目已有 job | 200，`job_ids` 只含新建的那部分 | 正常；缺的条目按下文对账取已有 job |
| 全部条目已有 job（含「上次其实成功了」的超时重试） | 400，`{"detail": "No tasks were resolved from input"}` | **按「已存在」处理，不是失败**；对账取已有 job |
| 全部条目已有 job，且同一组 items 的 run 上次中途失败 | 200，`created_count: 0`、`job_ids: []`（#501 治愈：run 回到 `created`，`run.created_count` 对齐 run 现有 job 数） | 正常；按 `run.id` 读回 job |

注意第二行是 400，不是 `created_count: 0` 的 200——后者只发生在第三行的
failed run 治愈路径（以及下文的并发重提）。识别「已存在」请严格比对
`detail` 字符串（400 还有别的含义，见错误码表）。

**对账.** 首次成功响应里的 `run.id` / `job_ids` 应当落库保存，它们是之后
轮询的主键。丢失时（超时没拿到响应、进程重启）按条目反查：

- material / bundle / ref 项：`GET /jobs/snapshot?search=<source_id>`，在
  结果里按 `source_type` + `source_id` **精确**匹配（`search` 是对 id /
  source_id / run_id / title 的子串匹配，可能多命中）。结果按创建时间倒序
  分页，旧提交可能不在第一页：沿 `next_cursor` 翻页，直到精确命中或
  `next_cursor` 为 null。翻完仍没命中说明该条目在本 workspace 没有 job
  （例如 job 已被删除），按「未提交」处理，不要当作已存在。
- text 项：material id 由服务端按内容派生，调用方不知道——带了
  `client_token` 时用 `GET /jobs/snapshot?search=~<client_token>`，在结果里
  按 `client_token` 字段精确匹配（token 由调用方生成，天然可对账），同样沿
  `next_cursor` 翻完全部页。精确命中多于一个 job 说明该 token 被复用于不同
  内容（违反上文「按内容版本唯一」）：**按错误处理，不要取第一个**——结果
  按创建时间倒序，第一个可能是另一份内容的 job，轮询 / 下载会拿到别的内容
  的产物；改用新 token 提交或人工核对。没带 token 时用 `GET /runs`（最近的 run 在前）按提交时间定位
  run，再 `GET /jobs?run_id=<run.id>` 取 job。需要可靠对账的调用方建议给
  text 项带 token，或先把文本作为 material 上传、再以 material 项提交。

**重试建议.**

- `POST /runs` 在条目粒度上幂等，网络超时 / 连接中断 / 5xx 后可以原样重提
  同一组 items：结果只会是 200（上次没落库，或补齐了剩余部分）或上表的
  「已存在」400，不会重复建 job。重试用指数退避（如 1s、2s、4s…，封顶
  数分钟）。
- 不要并发重提（上一个请求未返回就发下一个）。服务端在写侧保证同一条目
  只有一个 job，但两次并发调用都越过去重探测时，响应形态取决于两次的
  items 是否完全相同：
  - **完全相同的 items**：两次落到同一个确定性 run id（按 items 摘要派生），
    写入冲突按「同 run 重提」处理，两次响应都可能返回同一批 `job_ids` 和
    非零 `created_count`。
  - **不同的 items 但有重叠条目**：两次属于不同的 run，重叠条目只归先写入
    的 run，另一次的 `job_ids` 里没有它们（全部被抢走时是 200 +
    `created_count: 0` + 空 `job_ids`），缺的条目按上文对账取回。

  两种情形下调用方都应把所有响应的 `job_ids` 按 id 去重合并，不要累加
  `created_count`。串行重试最省事。
- 分块提交中途失败（大 run）返回 400，`detail` 是对象
  `{"message", "run_id", "created_so_far"}`：已建的 job 保留、run 标为
  failed；原样重提同一组 items 即从断点续建（已建的被去重跳过，run 治愈回
  `created`）。
- 轮询间隔建议 10 秒以上、长任务逐步放宽；单 job 状态优先轮询
  `GET /jobs/{job_id}`，批量看进度用 `GET /runs/{run_id}` 的 `job_stats`。

## 读取产物：清单、直连下载与全链路示例

External systems that submit jobs through the workspace API read results back
with three read-only endpoints, all scoped by the workspace in the URL path:

| 端点 | 作用 |
| --- | --- |
| `GET /api/workspaces/{workspace_id}/jobs/{job_id}` | 轻量状态：status/outcome/进度/产物名单 |
| `GET /api/workspaces/{workspace_id}/jobs/{job_id}/artifacts` | 产物清单（名字、形态、大小、content_hash、uploaded_at、媒体类型、#739 直连下载 URL 及其有效期） |
| `GET /api/workspaces/{workspace_id}/jobs/{job_id}/artifacts/{artifact_name}/raw` | 产物字节流（支持 `Range`，媒体类型按白名单；直连 URL 的兜底通道） |

前半程（签发 token、`POST /runs` 提交、按 `run_id` 列 job）见上文「最小
示例」，幂等/重试语义与错误码表见「幂等与重试」「错误码」；本节展开读取面，
节末给出照抄可跑的全链路示例。

**鉴权.** 与其它 workspace 端点同一守卫（`require_workspace_access`）：
会话 cookie 或 #626 的 workspace API token（`Authorization: Bearer <token>`
——Bearer 通道免 CSRF）。跨 workspace 的 job_id 一律 404（归属校验兼作
存在性校验，不能枚举其它 workspace 的 job）。#626 落地前的 scoped Bearer
token 同样可用：绑定了 `scoped_workspace_id` 的 token 只能读绑定 workspace
（不匹配同样 404，防枚举语义一致）。

**边界声明（legacy 裸路由）.** 控制台前端仍在用的不带 workspace 前缀的裸
路由（`GET /api/jobs/{job_id}`、`GET /api/jobs/{job_id}/artifacts/{name}`、
`.../raw`、`/runs/{run_id}/log`、`/token-usage`）挂 `require_job_workspace_access`
（#745）：按 job 行反查授权域，与前缀端点同一语义——绑定 `scoped_workspace_id`
的 token 只读绑定 workspace（跨 workspace 与未知 job 一律 404），成员按
membership（viewer 只读），全会话 admin 直接放行。外部系统的接入契约不变：
只用上表三个前缀端点。

**读取语义.**

- 产物优先从对象存储权威副本读取（`job_artifacts` manifest）——有
  manifest 行的产物，下载字节与清单公布的 `content_hash`/`uploaded_at`
  对应（本地 job_dir 缓存可能滞后于 manifest）；本地副本仅服务从未
  上传的 legacy 产物。对象存储未配置时清单降级为本地名并标
  `object_storage_enabled: false`。
- 产物名可以是 job_dir 相对子路径（`reports/final.json`）：清单列出
  的名字即下载 URL 里的名字（`{artifact_name:path}`）——按路径段
  percent-encode 后拼接（`#`/`?` 不编码会被客户端当 fragment/query
  截断；`/` 编成 `%2F` 或保持字面均可，服务端解码后仍按多段名匹配）；
  绝对名、`..` 段、反斜杠、`runs/` 前缀、点前缀段与含控制字符（含
  `%00`）或超长段（>200 字节）的名字一律 400。
- job 未完成时清单是空数组 + 当前 status（不是 404）——外部轮询以
  status 为准。
- 重跑后清单/读取都回答「当前最新」执行：`content_hash` 与
  `uploaded_at` 标识这次下载对应哪次执行（#508）。
- 对象被 bucket lifecycle 删除时 raw 下载 404（不是 500）。
- manifest 行的 `storage_key` 读侧强制校验本 job 的
  `jobs/{workspace}/{job_id}/` 前缀：行被污染/写歪（未来写入方失守、
  运维 SQL 误操作）时按 404 处理并记 warning，绝不读穿 workspace 边界。

**直连下载（#739）.** 清单里 `storage=object` 的条目带
`download_url`（presigned GET URL，指向对象存储，签名按
`AGENT_LEGION_S3_PUBLIC_ENDPOINT` 可达地址生成）和 `expires_at`（URL 失效
时刻，TTL 由实例设置 `agent_workers.artifact_download_presign_ttl_seconds`
控制，默认 3600 秒，重启生效）。大产物（视频等媒体）优先走 `download_url`
直连——字节流由 S3 直接应答，不占 Host 的连接、线程池与出口带宽，
与调度循环（claim/heartbeat）不再争资源：

直连 URL 与 raw 端点是同一份产物表示的两条通道。下表列出直连**继承**和
**不继承** raw 的哪些语义，对接方按此表实现，不要依赖表外行为：

| 语义 | raw 端点 | `download_url` 直连 |
| --- | --- | --- |
| 响应头 | 白名单 Content-Type；非白名单 `attachment`；`.gz` 对象附 `Content-Encoding: gzip` | **相同**：这三个头作为 S3 响应覆盖参数签进 URL，持有者改不了 |
| gzip 产物（#338，v4+ Worker 的产物都是这种） | 透传压缩字节 + `Content-Encoding: gzip` | **相同**；HTTP 客户端透明解码（`requests` 自动，curl 加 `--compressed`）。清单 `content_encoding: "gzip"` 标出存储形态 |
| 字节对应关系 | 名字下的**当前**产物（#508 重跑语义） | **不继承**（#853）：URL 固定到签发时的那个产物版本。TTL 内 job 重跑产出同名新字节后，旧 URL 返回旧字节或 404，绝不返回新字节；新字节要重取清单拿新 URL。清单的 `content_hash`（未压缩内容的 sha256）仍可用于校验 |
| 鉴权 | 每次请求校验 workspace API token | **不继承**：URL 是独立签名的持有者凭证。吊销 token 后 TTL 内仍可下载签发时的那个版本（不含之后重跑产生的同名新字节） |
| 有效期 | 不适用 | `expires_at` 是**上界**：签名凭据先失效（如 STS 临时凭据）时会提前 403。收到 403 或到达 `expires_at` 都重取清单，每次清单请求重新签发，URL 不落库 |
| Range | 支持（`.gz` 对象忽略 Range，返回全量） | 由 S3 处理；`.gz` 对象的 Range 落在压缩字节上（HTTP 语义如此），需要 seek 的媒体请走非 gzip 形态或 raw |

- **何时不用直连**：`download_url` 为 null 时一律回落 raw 端点，包括
  `local` 条目（从未上传对象存储）和未配置对象存储的实例
  （`object_storage_enabled: false`）。
- **public endpoint 未配置的形态**：实例只配 `AGENT_LEGION_S3_ENDPOINT`
  （无 `AGENT_LEGION_S3_PUBLIC_ENDPOINT`）时，`download_url` 非 null 但按
  内部端点签名，部署网络外不可达（连接超时或拒绝）。外部调用方对该形态
  应以 raw 端点兜底（直连请求失败即回落 raw），或由运维侧给实例配置
  public endpoint 后重启。
- **签名目标**：URL 的签名对象是服务端生成的 `storage_key`——#853 起每次
  写入落一次性版本 key `jobs/{workspace_id}/{job_id}/.v/{version}/{name}`
  （此前登记的存量产物保持 `jobs/{workspace_id}/{job_id}/{name}`，不迁移，
  也不再被任何写入覆盖）；key 一律由服务端生成，请求输入除 job_id 与产物
  名外无法影响签名目标；URL 只含 SigV4 签名参数，不含任何凭据。同名产物
  重新登记后被取代的旧版本对象随即删除，旧 URL 答 404（`NoSuchKey`）——
  与 403 一样按「重取清单」处理。设计与对象存储实测见
  [artifact-direct-url-pinning.md](architecture/artifact-direct-url-pinning.md)。
- **吊销 SOP**：吊销 token 不会让已签发 URL 失效。需要立即切断访问时，
  先把实例 TTL 调到最小（60 秒，重启生效，只约束之后签发的 URL），再删除
  相关 job。job 删除对对象存储是 **best-effort**：单个对象删除失败时 job
  仍删除成功，遗留对象等 bucket lifecycle 清理，期间已签发 URL 在 TTL 内
  仍可下载。因此真正的访问上限是「已签发 URL 的 TTL」，确认对象已删除
  才算切断（可在对象存储侧按 `jobs/{workspace_id}/{job_id}/` 前缀核对）。

**完整示例**（签发 token → 提交 → 轮询 → 下载）。以下两段示例与本文
上文的端点、幂等与重试、错误码一一对应（`tests/routes/test_external_integration_docs_contract.py`
把示例里的每个端点钉在 OpenAPI 契约与 api token 权限面上）。下载一步按上表
直连优先：`download_url` 为 null、已过 `expires_at` 或直连失败时回落 raw
端点。#626 的
workspace API token 唯一支持的提交面是 `POST /runs`：`/job-batches` 挂载
`reject_studio_agent_scope`，对包括 `actor_scope='api'` 在内的全部
scoped token 一律 403。

```bash
set -euo pipefail  # 任何一步失败立即停下，不带着空变量往下跑
HOST="https://agent-legion.example.com"
WS="my-workspace"
# 0) 签发 token（管理员会话；或控制台 workspace 设置 → 外部对接）。
#    明文只在这次响应里出现一次，落到调用方的密钥存储
WORKSPACE_API_TOKEN=$(curl -sS -X POST "$HOST/api/workspaces/$WS/api-tokens" \
  -H "Authorization: Bearer $ADMIN_SESSION" \
  -H "Content-Type: application/json" \
  -d '{"label": "cms-cron", "ttl_hours": 720}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["api_token"])')

# 1) 提交（items 引用已就位的 material/bundle/ref；一项一个 job）。
#    响应带 run.id 与本次新建的 job_ids（#735）；重复提交的 400 语义见
#    上文「幂等与重试」
HTTP=$(curl -sS -o submit.json -w '%{http_code}' -X POST "$HOST/api/workspaces/$WS/runs" \
  -H "Authorization: Bearer $WORKSPACE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"items": [{"type": "material", "material_id": "mat-1"}]}')
if [ "$HTTP" != 200 ]; then
  # 400「No tasks were resolved from input」= 全部条目已有 job（不是失败），
  # 按去重键反查已有 job 见下方 Python 示例的 find_existing_job；其它状态码
  # 的处理见下文错误码表
  echo "submit HTTP $HTTP: $(cat submit.json)" >&2
  exit 1
fi
RUN_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["run"]["id"])' < submit.json)
# job_ids 可能为空：#501 失败 run 治愈（created_count=0），或并发重叠提交时
# 条目归了别的 run——判空后按 run_id 读回
JOB_ID=$(python3 -c 'import json,sys; ids=json.load(sys.stdin)["job_ids"]; print(ids[0] if ids else "")' < submit.json)

# 2) 按 run 列 job（job_ids 为空或丢失时的读回路径；大 run 改用
#    /jobs/snapshot?run_id=…&limit=500 按 next_cursor 分页）
if [ -z "$JOB_ID" ]; then
  JOB_ID=$(curl -sS --fail "$HOST/api/workspaces/$WS/jobs?run_id=$RUN_ID" \
    -H "Authorization: Bearer $WORKSPACE_API_TOKEN" \
    | python3 -c 'import json,sys; jobs=json.load(sys.stdin)["jobs"]; print(jobs[0]["id"] if jobs else "")')
fi
if [ -z "$JOB_ID" ]; then
  # 该 run 下也没有 job：条目全被别的 run 抢走，按去重键反查（Python 示例）
  echo "run $RUN_ID has no jobs; reconcile by (source_type, source_id)" >&2
  exit 1
fi

# 3) 轮询状态直到终态 completed / failed（paused、awaiting_approval
#    是等待态，继续轮询）
while :; do
  STATUS=$(curl -sS --fail "$HOST/api/workspaces/$WS/jobs/$JOB_ID" \
    -H "Authorization: Bearer $WORKSPACE_API_TOKEN" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
  echo "status: $STATUS"
  case "$STATUS" in completed|failed) break;; esac
  sleep 15
done
if [ "$STATUS" = failed ]; then
  # 失败 job 可能没有产物；error_summary 是失败原因摘要
  curl -sS --fail "$HOST/api/workspaces/$WS/jobs/$JOB_ID" \
    -H "Authorization: Bearer $WORKSPACE_API_TOKEN" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["error_summary"])' >&2
  exit 1
fi

# 4) 取产物清单（content_hash / uploaded_at 区分执行；#739：object 条目
#    另带 download_url 直连地址 + expires_at 有效期）；这里取第一个产物
curl -sS --fail -o manifest.json "$HOST/api/workspaces/$WS/jobs/$JOB_ID/artifacts" \
  -H "Authorization: Bearer $WORKSPACE_API_TOKEN"
ARTIFACT=$(python3 -c 'import json,sys; a=json.load(sys.stdin)["artifacts"]; print(a[0]["name"] if a else "")' < manifest.json)
[ -n "$ARTIFACT" ] || { echo "job $JOB_ID has no artifacts" >&2; exit 1; }
# 直连地址：null（local 条目 / 未配置对象存储）或已过 expires_at 时输出空串
URL=$(python3 -c '
import json, sys
from datetime import datetime, timezone
a = json.load(sys.stdin)["artifacts"]
e = a[0] if a else None
live = e and e["download_url"] and datetime.fromisoformat(
    e["expires_at"].replace("Z", "+00:00")) > datetime.now(timezone.utc)
print(e["download_url"] if live else "")' < manifest.json)

# 5) 下载——直连优先（S3 直接应答，不穿 Host 代理），无直连地址或直连失败
#    （403：过期或签名凭据提前失效；public endpoint 未配置时网络外不可达）
#    回落 raw 端点。--compressed：gzip 产物两条通道都带 Content-Encoding:
#    gzip，curl 默认不解码。-f：失败以非零退出，不把错误体（S3 错误 XML、
#    lifecycle 回收后的 404）写成产物文件。
#    产物名按 URL 路径段 percent-encode（safe=""）：清单名里的 # 或 ?
#    不编码会被客户端当成 fragment/query 截断，服务端收到残缺名字。
NAME=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' \
  "$ARTIFACT")
OUT=$(basename -- "$ARTIFACT")
# 直连对象存储：不带 Authorization 头（S3 只按 URL 签名参数应答）
if [ -z "$URL" ] || ! curl -fsSL --compressed -o "$OUT" "$URL"; then
  curl -fsS --compressed -o "$OUT" \
    "$HOST/api/workspaces/$WS/jobs/$JOB_ID/artifacts/$NAME/raw" \
    -H "Authorization: Bearer $WORKSPACE_API_TOKEN"
fi
```

Python 等价（`requests`）：

```python
import hashlib, time, requests
from datetime import datetime, timezone
from urllib.parse import quote

ALREADY_EXISTS = "No tasks were resolved from input"  # 全部条目已有 job 的 400

s = requests.Session()
s.headers["Authorization"] = f"Bearer {WORKSPACE_API_TOKEN}"  # #626

def find_existing_job(
    source_type: str, source_id: str | None = None, client_token: str | None = None
) -> str | None:
    """按去重键反查已有 job：material / bundle / ref 项传完整 source_id；
    text 项的 material id 由服务端按内容派生、调用方不知道，带了
    client_token 时传 client_token（按 job 的 client_token 字段比对）。
    search 是子串匹配，须精确比对；结果按创建时间倒序分页，沿 next_cursor
    翻完全部页。token 须在 workspace 内按内容版本唯一（#910）：命中多个
    说明同一 token 复用于不同内容，无法判定对应哪份——报错，不取第一个。"""
    hits, cursor = [], None
    while True:
        r = s.get(
            f"{HOST}/api/workspaces/{WS}/jobs/snapshot",
            params={"search": source_id or f"~{client_token}", "limit": 500, "cursor": cursor},
        )
        if r.status_code == 429:  # 翻全量会耗限流额度：按 Retry-After 退避后重取同一页
            time.sleep(int(r.headers.get("Retry-After", "10")))
            continue
        r.raise_for_status()
        page = r.json()
        hits += [
            j["id"] for j in page["jobs"]
            if j["source_type"] == source_type
            and (j["source_id"] == source_id if source_id else j["client_token"] == client_token)
        ]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    if len(hits) > 1:
        raise RuntimeError(f"{client_token or source_id}: {len(hits)} jobs match; token reused")
    return hits[0] if hits else None


items = [{"type": "material", "material_id": "mat-1"}]
resp = s.post(f"{HOST}/api/workspaces/{WS}/runs", json={"items": items}, timeout=60)
if resp.status_code == 400 and resp.json().get("detail") == ALREADY_EXISTS:
    # 「已存在」不是失败（超时重试撞上了上次已成功的提交）：反查已有 job
    job_ids = []
else:
    resp.raise_for_status()
    body = resp.json()
    run_id, job_ids = body["run"]["id"], body["job_ids"]  # #735
    if not job_ids:
        # #501 治愈路径（created_count=0）：按 run_id 读回该 run 的 job
        job_ids = [
            j["id"] for j in s.get(
                f"{HOST}/api/workspaces/{WS}/jobs", params={"run_id": run_id}
            ).json()["jobs"]
        ]
if not job_ids:
    # 400「已存在」，或并发重叠提交时条目归了别的 run：按去重键反查
    # （带 client_token 的 text 项：find_existing_job("material", client_token=…)）
    found = find_existing_job("material", "mat-1")
    if found is None:
        # 翻完也没有：该条目在本 workspace 没有 job（期间被删除等），
        # 当作未提交处理——交给上层决定重提，不要当成已存在
        raise RuntimeError("mat-1: no existing job found; resubmit")
    job_ids = [found]
job_id = job_ids[0]

# 终态只有 completed / failed；paused、awaiting_approval 是等待态
while True:
    job = s.get(f"{HOST}/api/workspaces/{WS}/jobs/{job_id}").json()
    if job["status"] in {"completed", "failed"}:
        break
    time.sleep(15)
if job["status"] == "failed":
    # 失败 job 可能没有产物；error_summary 是失败原因摘要
    raise RuntimeError(f"job {job_id} failed: {job['error_summary']}")

def live_url(entry) -> str | None:
    """直连地址：null（local 条目 / 未配置对象存储）或已过 expires_at 时为 None。"""
    if not entry["download_url"]:
        return None
    expires = datetime.fromisoformat(entry["expires_at"].replace("Z", "+00:00"))
    return entry["download_url"] if expires > datetime.now(timezone.utc) else None


listing = s.get(f"{HOST}/api/workspaces/{WS}/jobs/{job_id}/artifacts")
listing.raise_for_status()
manifest = listing.json()
for entry in manifest["artifacts"]:  # 可能为空数组：job 没有产出产物
    # 两条通道分开走：Host API 用带 Bearer 的 session；presigned 直连下载
    # 必须用不带任何会话头的独立请求——requests.Session 的会话级头会合并进
    # 每个请求（不区分目标主机），直接 s.get(download_url) 会把 workspace
    # API token 原样发给对象存储主机。S3 只按 URL 里的 SigV4 签名参数应答。
    blob = None
    url = live_url(entry)
    if url is not None:
        try:
            direct = requests.get(url, timeout=60)  # 无鉴权头；gzip 产物自动解码
            # 403 = 过期或签名凭据提前失效；404 = 签发后该版本已被重跑取代（#853）
            if direct.status_code in (403, 404):  # 重取清单再试一次
                fresh = s.get(f"{HOST}/api/workspaces/{WS}/jobs/{job_id}/artifacts").json()
                for e in fresh["artifacts"]:
                    if e["name"] == entry["name"]:
                        entry = e
                url = live_url(entry)
                direct = requests.get(url, timeout=60) if url else None
            if direct is not None and direct.ok:
                blob = direct.content
        except requests.RequestException:
            pass  # public endpoint 未配置时直连网络外不可达：回落 raw
    if blob is None:
        # safe=""：名字里的 # 或 ? 必须 percent-encode——否则 # 起被当作
        # fragment、? 起被当作 query，服务端收到截断后的名字（子路径名的 /
        # 被一并编成 %2F 也无妨：服务端解码后仍按多段名走 {artifact_name:path}）
        raw = s.get(
            f"{HOST}/api/workspaces/{WS}/jobs/{job_id}/artifacts/{quote(entry['name'], safe='')}/raw"
        )
        if raw.status_code == 404:
            # 对象已被 bucket lifecycle 回收：记录后跳过，不要把错误体当产物存下
            continue
        raw.raise_for_status()
        blob = raw.content
    # content_hash 是未压缩内容的 sha256：raw 返回名字下的当前字节、直连
    # 返回签发时的版本，期间若发生重跑 raw 就会与清单不一致，此时重取清单
    # （local 条目没有 content_hash，跳过校验）
    if entry["content_hash"] and hashlib.sha256(blob).hexdigest() != entry["content_hash"]:
        raise RuntimeError(f"{entry['name']}: bytes changed since manifest (rerun?) — re-fetch")
```

## 错误码

错误响应体是 FastAPI 标准形态 `{"detail": …}`（多数为字符串；分块失败与
请求校验失败为对象 / 数组）。

| 状态码 | 典型 `detail` / 场景 | 是否重试 |
| --- | --- | --- |
| 400 | `No tasks were resolved from input`：全部条目已有 job | 不重试；按「已存在」对账（见上节） |
| 400 | `{"message", "run_id", "created_so_far"}`：分块提交中途失败 | 原样重提同一组 items 续建 |
| 400 | 其它输入错误：素材未就绪（`Material is not ready` / bundle 成员未全部就绪）、workspace 无已发布 workflow、items 超过实例上限 `workflows.max_items_per_run`、节点配置无效、非法产物名 | 修正输入；素材未就绪可等上传完成后重提 |
| 401 | `Not authenticated`（缺 Authorization）、`Session expired or revoked`（token 吊销 / 过期 / 格式错） | 不重试；重新签发 token |
| 403 | `Scoped tokens cannot take effect` 等：调用了不对 token 开放的 effecting / 管理 / 用户端点（如 `POST /job-batches`、legacy `/api/jobs/{job_id}` 变更端点） | 不重试；改用上表端点 |
| 404 | `Workspace not found`：URL 里的 workspace 与 token 绑定的不一致、workspace 不存在、或端点不在 token 权限面内 | 不重试；三种情形刻意同形态（防枚举），检查 URL 与 token 是否配套 |
| 404 | `Job not found` / `Run not found`：不存在或属于别的 workspace（同样防枚举）；`Artifact not found`：产物不存在或对象已被 bucket lifecycle 回收 | 不重试 |
| 404 | `Material not found: …` / `Material bundle not found: …`：`POST /runs` 引用了本 workspace 没有的素材 | 不重试；修正 items |
| 409 | text 项内容与一个未就绪（上传未完成）的 material 同 hash | 完成或删除那个 material 后重试 |
| 422 | 请求体 / 参数校验失败：items 为空、未知字段（含 ref 项带 `client_token`）、`type` 不在四种之内、`client_token` 超长 / 含非法字符；`GET /runs` 与 `GET /jobs/snapshot` 的 `limit` 不在 1–500、`GET /jobs` 的 `limit` 不在 1–2000；`GET /jobs/snapshot` 的 `cursor` 无法解析（不是上一页原样返回的 `next_cursor`：缺分隔符、时间戳非法等，#891）；`run_id` / `status` 过滤传空串；参数类型不对（如 `limit=abc`） | 不重试；修正请求 |
| 429 | per-token 限流命中（#738）：超出该 token 的令牌桶，响应带 `Retry-After`（秒，按补充速率向上取整） | 按 `Retry-After` 退避后重试；批量轮询改用 `/jobs/snapshot` 一次取整批，降低请求频率 |
| 503 | text 项需要对象存储，实例未配置时返回 | 稍后重试或联系管理员 |
| 5xx | 服务端异常 | 指数退避重试；`POST /runs` 重试安全（见上节） |
