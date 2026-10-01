# 外部系统提交条目：Workspace API Token（issue #626）

外部系统（CMS、表单后端、定时任务、其他 agent）可以凭 workspace 级
API token 免登录提交条目创建 job，无需人工登录控制台。token 是
machine-to-machine 凭据：绑定且仅绑定一个 workspace，权限是 editor 的
「提交条目 + 读运行状态」最小集——除了 runs 提交面与只读查询外的一切
端点（管理面、workflow 定义、studio-agent 工具面、其它 effecting 操作）
对它一律拒绝（workspace 路由 404，管理端点 403）。

## 最小示例

1. **签发**（管理员在控制台：workspace 设置 → Agent 与 Worker → 签发
   API Token；或直接调管理 API）。明文 token 只显示一次，立即保存：

   ```bash
   curl -X POST "$HOST/api/workspaces/$WORKSPACE_ID/api-tokens" \
     -H "Authorization: Bearer $ADMIN_SESSION" \
     -H "Content-Type: application/json" \
     -d '{"label": "cms-cron", "ttl_hours": 720}'
   # → {"token_id": "…", "api_token": "<token_id>.<secret>", …}
   ```

2. **提交条目**（一个 run，每项一个 job；`ttl_hours` 可省略表示不过期）：

   ```bash
   curl -X POST "$HOST/api/workspaces/$WORKSPACE_ID/runs" \
     -H "Authorization: Bearer $API_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"items": [{"type": "material", "material_id": "mat-1"}]}'
   # → {"run": {"id": "…", "status": "created", …}, "created_count": 1}
   ```

3. **查状态**（只读；workspace 调度暂停时提交照常排队，job 状态表达等待）：

   ```bash
   curl "$HOST/api/workspaces/$WORKSPACE_ID/runs" \
     -H "Authorization: Bearer $API_TOKEN"
   curl "$HOST/api/workspaces/$WORKSPACE_ID/runs/$RUN_ID" \
     -H "Authorization: Bearer $API_TOKEN"
   curl "$HOST/api/workspaces/$WORKSPACE_ID/jobs" \
     -H "Authorization: Bearer $API_TOKEN"
   # 一个 run 的 job 可能远超 500 条：legacy /jobs 固定只返回最近 500 条，
   # 分页/按 run 查询走 /jobs/snapshot（limit ≤ 500 + next_cursor 循环；
   # run_id 来自创建响应）。
   curl "$HOST/api/workspaces/$WORKSPACE_ID/jobs/snapshot?run_id=$RUN_ID&limit=500" \
     -H "Authorization: Bearer $API_TOKEN"
   ```

Bearer 通道不需要 CSRF header（非 ambient 凭据）。token 泄露时在设置面板
吊销，使用中的调用立即 401。

## 语义与边界

- **格式**：`{token_id}.{secret}`，与 worker register token 相同的存储
  约定——库内只存 sha256，明文只在签发响应出现一次。支持签发时指定
  `ttl_hours` 过期时间，支持随时吊销（软吊销，行保留审计）。
- **权限面**：`POST /runs`（提交）与 `GET /runs` / `GET /runs/{id}` /
  `GET /jobs`（legacy，最近 500 条）/ `GET /jobs/snapshot`（分页 +
  `run_id` 过滤，只读查询），外加 #631 外部读取面的三个 GET（
  `GET /jobs/{job_id}` 状态、`GET /jobs/{job_id}/artifacts` 清单、
  `GET /jobs/{job_id}/artifacts/{artifact_name}/raw` 下载——提交后轮询
  与取产物走它们，见
  [remote-execution-runbook.md](remote-execution-runbook.md) §9）。跨
  workspace 访问与其它 workspace 路由一律
  404（与不存在同一形态，不可枚举）；管理端点 403；token 不能签发新
  token、不能改 workflow 定义。
- **审计**：经 API token 提交的 run 在服务端结构化日志里记录 token_id
  （不冒充任何用户身份）；列表展示 `last_used_at` 最近使用水位（每分钟
  至多刷新一次；吊销后的重试也刷新水位，但仅当调用方持有正确 secret——
  只知道 token_id 的错误凭据刷不动它）。
- **不做的事（初版）**：per-token 速率限制/配额（后续项）；管理员面
  按标签检索。
