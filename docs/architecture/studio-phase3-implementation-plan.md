# 阶段 3 实施计划：Studio 内置 agent + workflow 调度热刷新

状态：实施中（2026-08-13 启动）。chunk 1→5 顺序执行，每 chunk 质量门全绿后进入下一个。
上游设计：`workflow-studio-evolution-design.md`（§1 定位、§4 已定案决策）。

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

## Chunk 4 · 内置 agent 运行时 + 对话 API

- 外部前置（非本 repo，需用户在 skill repo 配合）：`studio_authoring` skill +
  tag + relock。
- 新建 `services/studio_chat.py`（session/message 持久化）、
  `services/studio_agent_runner.py`（后台线程 PiRunner.run(persist_run=False)，
  transcript 重放拼 prompt，env 注入 scoped token + API base URL，工具脚本经
  skill_dir 提供；取消复用 SubprocessTracker；token 不落日志/命令行）。
- 新建 `routes/studio_chat.py`：sessions CRUD、POST message 启动 run、GET 状态/
  消息；`require_workspace_access`。
- 内置 agent 定义启动 seed（runtime 建议 velites）。
- DDL：schema v42，`studio_chat_sessions` / `studio_chat_messages`（tool_events
  jsonb、token usage 列）。
- 测试：transcript 拼装、run 状态机（fake PiRunner）、对话 API。

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
