# 阶段 3 实施计划：Studio 内置 agent + workflow 调度热刷新

状态：实施中（2026-08-13 启动）。chunk 1→5 顺序执行，每 chunk 质量门全绿后进入下一个。
上游设计：`workflow-studio-evolution-design.md`（§1 定位、§4 已定案决策）。

## 方向修正（2026-08-14，用户拍板）

chunk 4/5 的「内嵌 pi/velites 运行时 + transcript 重放」方案被 **MCP/ACP 三层分离**
取代，职责拆开：

1. **MCP server（工具面）**：平台自带 stdio MCP server（`server.app.mcp_server`），
   薄包装 chunk 3 的 8 个工具端点，任何支持 MCP 的 agent 配上 scoped token 即可
   authoring。**已落地**：merge `19a4d4f8`（token 自助签发 API + schema v42
   `auth_scoped_tokens.origin/id` + MCP server + 接入文档 `docs/studio-agent-mcp.md`）。
   token 管理端点叠加 `reject_studio_agent_scope`，run 级短 token 不能铸 user 级
   长 token（权限延长通道已堵）。
2. **ACP（对话面）**：Studio 对话后端改为 ACP client，接任何支持 ACP 的 agent
   （用户可选），替代原内嵌运行时的 chunk 4。`studio_authoring` skill 不再是
   硬前置（authoring 规范经 MCP instructions/prompt 注入）。
3. **对话前端（交互面）**：chunk 5 不变（Studio 右栏面板）。

原 §0 决策 1（内置 agent 定义 seed）与 6（PiRunner run 执行）据此作废；
决策 2/3/4（工具面形态、scoped token、草稿落库）继续有效且已被 MCP server 复用。
ACP 生态验证（哪些 agent 支持、协议成熟度）是 chunk 4 改造的第一步。

## 0. 主会话已拍板的缺口决策（不得重开）

1. **内置 agent 定义存储**：builtin seed 进 `versioned_entities`（参照 executor
   `builtin_definitions.py` 出厂目录模式，启动 seed-if-absent，admin 可演进不被
   覆盖）。其 skill 走外部仓库 + skill_sources/skill_lock 评审链——**外部前置
   任务**（非本 repo）：skill repo 新增 `studio_authoring` skill + tag + relock。
2. **工具面形态**：专用工具端点组 `/api/studio-agent/tools/*`（HTTP 自调用，
   agent 经 skill 脚本 + bash 调用），只暴露 draft/validate/register-request，
   不给 agent 全量用户 token。
3. **鉴权身份**：每次对话 run 由后端铸造短期 scoped token（绑定发起用户 +
   `actor_scope=studio_agent` + TTL≈run 时长+宽限），**落新表
   `auth_scoped_tokens`**（不扩 sessions 表）；生效端点（publish/rollback/
   archive/delete）加 `reject_studio_agent_scope` 依赖显式拒绝。双保险：工具面
   不含生效端点 + 生效端点显式拒绝。登记 STUDIO-AGENT-001 invariant + 证据矩阵。
4. **草稿落库形态**：workflow 定义草稿维持客户端 YAML（agent 工具返回 validate
   过的 YAML → 前端「应用到编辑器」灌入 useWorkflowStudioDraft），不新增服务端
   workflow draft 表；节点代码/agent 定义草稿落 versioned_entities draft（现有
   API），`created_by=f"studio-agent:{user_id}"`。
5. **面板形态**：Studio 右栏新 tab（复用 WorkflowStudioRightPanel 结构），run
   状态轮询或 SSE（`routes/workspaces.py:84` 先例），不建全屏对话页。
6. **run 执行**：Host 本地后台线程，`PiRunner.run(persist_run=False)`，多轮用
   无状态 transcript 重放（pi 无 session 复用）；run 记录落库；不走 Job/worker
   调度体系。
7. **热刷新触发**：推送式——注册 API 成功后调 `worker.reload_scan_entries()` +
   `notify_schedulable_work()`；不加轮询 reconcile。多进程局限与 S0a 一致，
   可接受。
8. **register 工具端点**：复用现有 `_KEY_PATTERN` 校验即可，不加 `studio_` 前缀
   约定。注册不产生 definition、无调度效果直到人发布首个 revision，风险可控。
9. **dry-run 不在本阶段**：工具面不预留 dry-run 端点。

## Chunk 1 · workflow 调度热刷新（独立可先落）

- `server/app/workflow_worker/thread.py`：新增 `reload_scan_entries()`——线程外
  构建新 `(definitions, definitionless_keys)`，单次 tuple 换态（参照
  `executors/registry.py:66` 不可变快照模式）；`start()` 改调同一方法。
- `server/app/main.py`（:117-159）：`app.state.workflow_worker = thread`（路由取
  句柄）。
- `server/app/routes/workflow_catalog_admin.py`：注册事务**成功提交后**调
  `reload_scan_entries()` + `notify_schedulable_work()`。
- `catalog_scan.py` docstring 删「需重启」表述。
- 测试：`tests/workflow_worker/test_scan_hot_reload.py`（运行中注册新 key →
  下一 pass 即扫描，含 definitionless 与带 definition 两种；reload 与 poll 交叉
  的并发安全；注册失败不触发刷新）+ 注册路由调用 reload 的契约测试。
- 风险：共享状态必须整体换态，禁止原地 mutate（AGENTS.md 高发缺陷族）。

## Chunk 2 · studio-agent scope 鉴权底座（安全敏感面，先于工具面）

- 新建 `server/app/auth/scoped_tokens.py`：mint/校验短期 scoped token；
  `auth/dependencies.py` 加 `reject_studio_agent_scope`。
- 生效端点挂拒绝依赖：workflow publish、节点代码 publish/rollback/archive、
  agent/executor definitions publish/rollback/archive/delete。
- DDL：schema v41，`auth_scoped_tokens` 表（user_id、scope、expires_at、
  revoked）。改 DDL 的测试 `@pytest.mark.fresh_schema`。
- 测试：scope 强制契约测试用端点清单枚举兜底（scoped token 打全部生效端点
  →403，打 draft/validate →放行）；token 过期/伪造。
- 治理：STUDIO-AGENT-001 invariant + 证据矩阵登记（本 chunk 完成）。

## Chunk 3 · 工具面 API（`/api/studio-agent/tools/*`）

- 新建 `routes/studio_agent_tools.py` + contracts + `services/studio_agent_tools.py`。
  端点仅限：validate_workflow（复用 `workflow_draft_publish.py`）、
  compare_workflow_draft、save_node_code_draft（created_by 带 studio-agent 前缀）、
  save_agent_definition_draft、register_workflow（成功后挂 chunk 1 刷新）、
  read 类（active revision、catalog 列表、节点代码现状）。
- 全部端点要求 scoped token，不挂 require_admin。
- 测试：`tests/routes/test_studio_agent_tools.py` 全端点行为 + 身份归属 + 冲突。
- api.ts 重新生成（api:check 必须过）。

## Chunk 4 · 对话后端（ACP client 化，方向修正后）

**已落地（studio-acp 分支，schema v43）**：

- ACP client 化：每对话 session 一个 ACP 子进程（`server/app/studio_chat/acp_session.py`，
  acp SDK + 专属 asyncio loop 线程，生命周期对齐 WorkflowWorkerThread 模式；cancel 经
  `loop.call_soon_threadsafe` 直发，close 先优雅排水再 kill 并重新校验进程身份）。
- agent 注册表：实例级 `global_settings` key `studio_agents`（{api_base, agents[]），
  admin 经 `GET/PUT /api/admin/studio-agents` 维护；非 admin 只能按 id 选，
  picker API 不出 command/args（防 RCE），且只列本机可用的 agent——可用性探测
  为 PATH 级（shutil.which，不做 ACP 握手），60s TTL 缓存，后端启动时预热并
  对缺失项告警；用不可用 agent 建 session 在 spawn 前明确报错、不留孤儿行。
  注册表独立成文档而非并入 `instance` 大文档：后者是整体替换语义且前端按字段
  重建 payload，并入会被无关保存冲掉。
- session/new 现铸 scoped token（origin='run'，TTL 2h 固定）经 MCP env 注入，
  只出现在 session/new 请求里；session 关闭即吊销；不落库/日志/消息/SSE。
- schema v43：`studio_chat_sessions`（capability 快照、allow_all_permissions、
  mcp_status）+ `studio_chat_messages`（kind: text/tool_call/plan/permission/status，
  identity seq 排序）；升级语句在 schema 文件 + migrations 幂等兜底。
- 路由（workspace 下，`require_workspace_access`）：sessions CRUD、POST/GET messages、
  SSE `/events`（复用 JobEventManager，channel `studio-chat:{session_id}`，
  事件类型 message/session）、POST cancel、POST permissions/{request_id}、
  POST permissions/allow-all。
- permission 策略：agent-legion MCP 工具 auto-approve；其余默认转发人确认，
  session 级 allow-all 开关；cancel 以 denied 结清挂起 permission。
- MCP 可见性冒烟：run 结束未观察到 agent-legion 工具调用 → mcp_status=unverified
  + status 消息警告，不静默。
- 内置 prompt 引导：`studio_chat/prompts.py` STUDIO_AUTHORING_BOOTSTRAP，
  每 session 首个 prompt 前缀注入。
- v1 不做：前端面板（chunk 5）、loadSession 持久恢复（进程内 session）。

### Chunk 4 Quality Impact

- 新 invariant STUDIO-AGENT-002（注册表唯一命令源、token 边界、permission 策略），
  证据：tests/routes/test_studio_chat.py、test_studio_agents_admin.py、
  tests/services/test_studio_chat_service.py、tests/db/test_studio_chat_schema.py。
- file_budget 豁免（schema.py / migrations/__init__.py / main.py /
  routes/__init__.py）为按设计增长的组合点，ceiling 随 schema 版本滚动登记。
- DDL 变更测试 `@pytest.mark.fresh_schema`，覆盖 v42 旧库升级 v43 路径。
- 真机 agent 冒烟不进 CI：手动脚本 scripts/studio_chat_smoke.py。

原方案要点（备查）：`services/studio_chat.py` 会话持久化、后台线程 run、
无状态 transcript 重放、env 注入 scoped token——ACP 化后 transcript/进程
管理由 ACP 协议接管，会话持久化与 run 状态机仍是我们的责任。

## Chunk 5 · 前端对话面板 + 收尾

- 新建 `frontend/src/pages/workflowStudio/chat/`（面板、消息列表、run 状态轮询），
  挂进 WorkflowStudioRightPanel；「应用到编辑器」灌入 useWorkflowStudioDraft。
- transport types 从生成的 api.ts 派生，不手写。
- 测试：chat 组件逻辑测试；视覆盖加 e2e-smoke「对话产草稿→应用→validate」。
- 行数预算：新代码一律进 chat/ 子目录新文件，不抬 ceiling。

## 横切

- chunk 顺序 1→2→3→4→5；chunk 1 完全独立。
- 每 chunk `./scripts/check-quick.sh` 全绿（等结果再交差）；测试放对子目录。
- 多步变更先备妥再统一应用；禁止半应用状态。
