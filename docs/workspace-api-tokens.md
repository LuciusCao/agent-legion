# 外部系统提交条目：Workspace API Token（issue #626）

外部系统（CMS、表单后端、定时任务、其他 agent）可以凭 workspace 级
API token 免登录提交条目创建 job，无需人工登录控制台。token 是
machine-to-machine 凭据：绑定且仅绑定一个 workspace，权限是 editor 的
「提交条目 + 轮询状态 + 下载产物」最小集——除了 runs 提交面、只读查询
与产物读取外的一切端点（管理面、workflow 定义、studio-agent 工具面、
其它 effecting 操作）对它一律拒绝（workspace 路由 404，与不存在同形态；
部分 effecting / 管理 / 用户端点 403——两者都是终局拒绝）。

本文是对接契约（端点、请求/响应形态、幂等与重试、错误码）；照抄可跑的
端到端脚本（curl 与 Python，签发 → 提交 → 轮询 → 下载）与产物读取语义见
[remote-execution-runbook.md](remote-execution-runbook.md) §9。

## 最小示例

1. **签发**（管理员在控制台：workspace 设置 → Agent 与 Worker → 签发
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
   sha256 落成 material；`filename` 须以 `.md` / `.txt` 结尾）。
   material/bundle 必须已上传就绪——items 只引用已有素材，本通道不收文件。

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
   # 大 run 分页：limit 默认 200，按 next_cursor 循环到 null。越界的 limit
   # 不报错，而是被静默钳到 1–500 并照常 200——以实际返回条数和
   # next_cursor 为准，不要假设拿到了请求的条数
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
   按 `run_id` 过滤的 jobs 列表）：

   ```bash
   # 产物清单：含 storage/content_hash/size_bytes/uploaded_at/media_type
   curl "$HOST/api/workspaces/$WORKSPACE_ID/jobs/$JOB_ID/artifacts" \
     -H "Authorization: Bearer $API_TOKEN"
   # raw 字节下载（产物名按 URL 路径段 percent-encode；Range 请求答 206）
   curl "$HOST/api/workspaces/$WORKSPACE_ID/jobs/$JOB_ID/artifacts/$ARTIFACT_NAME/raw" \
     -H "Authorization: Bearer $API_TOKEN"
   ```

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
  | `GET /jobs/snapshot` | 分页 job 列表（`run_id` 等过滤，`limit` 钳到 1–500 + `next_cursor`） |
  | `GET /jobs/{job_id}` | 单 job 轻量状态 |
  | `GET /jobs/{job_id}/artifacts` | 产物清单 |
  | `GET /jobs/{job_id}/artifacts/{artifact_name}/raw` | 产物字节流 |

  后三个的读取语义见
  [remote-execution-runbook.md](remote-execution-runbook.md) §9。跨
  workspace 访问与其它 workspace 路由一律 404（与不存在同一形态，不可
  枚举）；部分 effecting / 管理端点 403；token 不能签发新 token、不能改
  workflow 定义。
  旧的 `POST /job-batches` 提交面不对任何 scoped token 开放（403），外部
  系统只走 `POST /runs`。
- **审计**：经 API token 提交的 run 在服务端结构化日志里记录 token_id
  （不冒充任何用户身份）；列表展示 `last_used_at` 最近使用水位（每分钟
  至多刷新一次；吊销后的重试也刷新水位，但仅当调用方持有正确 secret——
  只知道 token_id 的错误凭据刷不动它）。
- **不做的事（初版）**：per-token 速率限制/配额（#738，尚未落地）；管理员
  面按标签检索。

## 幂等与重试

**去重键.** 一个条目在 workspace 内只建一次 job：去重键是 job 的
`(source_type, source_id)`——material 项为 `("material", material_id)`、
bundle 项为 `("bundle", bundle_id)`、ref 项为
`("ref", "<connection_key>:<external_id>")`、text 项按内容 sha256 复用同一
material，等同 material 项。去重跨 run 生效：之前任何 run 已为该条目建过
job，再次提交就不会新建（要重新执行同一条目走控制台的重跑，不是重提交）。

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
- text 项：material id 由服务端按内容派生，调用方不知道——用
  `GET /runs`（最近的 run 在前）按提交时间定位 run，再
  `GET /jobs?run_id=<run.id>` 取 job。需要可靠对账的调用方建议先把文本作为
  material 上传，再以 material 项提交。

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

## 错误码

错误响应体是 FastAPI 标准形态 `{"detail": …}`（多数为字符串；分块失败与
请求校验失败为对象 / 数组）。

| 状态码 | 典型 `detail` / 场景 | 是否重试 |
| --- | --- | --- |
| 400 | `No tasks were resolved from input`：全部条目已有 job | 不重试；按「已存在」对账（见上节） |
| 400 | `{"message", "run_id", "created_so_far"}`：分块提交中途失败 | 原样重提同一组 items 续建 |
| 400 | 其它输入错误：素材未就绪（`Material is not ready` / bundle 成员未全部就绪）、workspace 无已发布 workflow、items 超过实例上限 `workflows.max_items_per_run`、节点配置无效、`workflow_key` 与 workspace 不符、非法产物名 | 修正输入；素材未就绪可等上传完成后重提 |
| 401 | `Not authenticated`（缺 Authorization）、`Session expired or revoked`（token 吊销 / 过期 / 格式错） | 不重试；重新签发 token |
| 403 | `Scoped tokens cannot take effect` 等：调用了不对 token 开放的 effecting / 管理 / 用户端点（如 `POST /job-batches`、legacy `/api/jobs/{job_id}` 变更端点） | 不重试；改用上表端点 |
| 404 | `Workspace not found`：URL 里的 workspace 与 token 绑定的不一致、workspace 不存在、或端点不在 token 权限面内 | 不重试；三种情形刻意同形态（防枚举），检查 URL 与 token 是否配套 |
| 404 | `Job not found` / `Run not found`：不存在或属于别的 workspace（同样防枚举）；`Artifact not found`：产物不存在或对象已被 bucket lifecycle 回收 | 不重试 |
| 404 | `Material not found: …` / `Material bundle not found: …`：`POST /runs` 引用了本 workspace 没有的素材 | 不重试；修正 items |
| 409 | text 项内容与一个未就绪（上传未完成）的 material 同 hash | 完成或删除那个 material 后重试 |
| 422 | 请求体 / 参数校验失败：items 为空、未知字段、`type` 不在四种之内；`GET /runs` 的 `limit` 不在 1–500、`GET /jobs` 的 `limit` 不在 1–2000；`run_id` / `status` 过滤传空串；参数类型不对（如 `limit=abc`）。注意 `GET /jobs/snapshot` 的 `limit` 越界**不是** 422，而是静默钳到 1–500 | 不重试；修正请求 |
| 429 | 这些端点**尚未限流**（per-token 限流 #738 未落地），目前不会返回 | 建议客户端预先按 `Retry-After` 退避处理 429，限流落地后无需改动 |
| 503 | text 项需要对象存储，实例未配置时返回 | 稍后重试或联系管理员 |
| 5xx | 服务端异常 | 指数退避重试；`POST /runs` 重试安全（见上节） |
