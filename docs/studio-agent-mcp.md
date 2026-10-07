# Studio Agent MCP Server

把平台的 studio-agent 工具面（`/api/studio-agent/tools/*`）以 MCP stdio server 的形式暴露给任意外部 agent（Kimi Code、Claude Code 等）。外部（自助）agent 拿到 33 个工具；Studio 对话内绑定会话时是 35 个（多 `get_studio_context` / `get_job_context` 两个会话绑定工具，无会话绑定时它们不注册，#660）。工具名的权威清单是 `server/app/mcp_server/tool_names.py`（与注册结果由测试钉成全等），下面按组说明语义。

workflow、预览面板与节点代码 / prompt 相关工具都是**读取 / 校验 / 草稿级**：workflow 发布、回滚、归档与预览面板发布等生效操作永远由人在 Studio 里完成（见下方「权限边界」）。**Skill 写工具例外**（`save_skill_version`、`sync_shared_materials`，以及 `create_skill` 的初始 commit）：它们直接提交到 in-place skill 仓库并推进 HEAD，而节点 `skill.ref` 为空或 `latest` 时每次 dispatch 都现读该仓库 HEAD（`server/app/skills/manager.py` 的 `checkout_skill`），所以这些写入不经 Studio 发布、下一次 dispatch 即对引用该 skill 的 `latest` 节点生效；只有 pin 到具体 tag 的节点不受影响（重锁定仍是 `make skills-lock` 人操作）。workflow 没有注册概念，它就是 workspace 内部的一份 DAG（workspace id 即 workflow key）。

**创作指引与会话上下文**

- `get_authoring_guide`：内置创作 playbook（本地静态资源，不发 HTTP）。默认返回全文，可传 `section` 按章取：`tool-map` / `flow` / `yaml` / `capabilities` / `agents` / `skills` / `errors`。
- `get_studio_context`：读会话上下文，含画布当前未发布的 draft YAML（仅 Studio 对话内绑定会话时注册）。

**Workflow（读取、草稿、发布请求）**

- `get_active_workflow`：读激活 revision；无已发布 revision 时返回结构化空态 `{"state": "empty"}`，不报错。
- `validate_workflow` / `compare_workflow`：校验、对比草稿；无基线时 compare 退化为草稿全貌预览。
- `get_workflow_draft` / `save_workflow_draft`（#633）：读写画布同一份未发布草稿。读取无草稿时返回结构化空态（双 null）；保存以 CAS 语义写回完整 `definition_yaml`——携带上次读取的 `updated_at` 作 `expected_updated_at`（无草稿时用字面量 `never-saved`），过期即 409 且 detail 携带当前草稿（`current_draft.definition_yaml` / `current_draft.updated_at`），据此 rebase 重试而非静默覆盖。保存后画布与 YAML 编辑器经 turn-end 查询失效同步显示；编辑只落草稿。
- `request_workflow_publish`（#416）：只是「请求」——校验 workspace 未发布草稿通过后挂一条 pending 记录，由人在 Studio 的发布确认对话框里确认/取消，工具本身永不产生 revision；确认走与手动发布完全一致的门禁。
- `get_publish_request_status`：查询发布请求结果（pending / confirmed / rejected / expired / superseded）。

**节点代码与节点 prompt**

- `get_node_code`：读节点代码现状（builtin 源码、已发布自定义代码与待发布草稿）。
- `save_node_code_draft`：存节点代码草稿；`expected_capability` 声明支持新节点骨架草稿。
- `get_node_prompt`：预览节点运行 prompt（平台信封 + 节点指令段；`execution.prompt` 为空时是自动组装的默认指令，非空则整段替代）。
- `save_node_prompt`：写进未发布 draft YAML 的 `nodes.<key>.execution.prompt`，空串清除回默认。

**Agent 定义**（#633；#935 起 Agent 定义只读，见 [remote-execution-runbook.md §6](remote-execution-runbook.md#6-migrating-an-agent-node-between-runtimes-pi--velites)）

- `get_agent_definitions`（只读）：workspace 的历史 Agent 定义清单，每个 Agent 取最新版本，携带全部字段与版本元数据（version / status / definition_hash / created_by / created_at / published_at）；#440 起 Agent 定义不再充当节点执行档案，只读供追溯。
- `get_runtime_models`（只读）：workspace 在线 Worker 声明聚合出的 `{runtime: {provider: [models]}}` 视图。
- `get_agent_runtimes`（只读）：每个 runtime（pi / velites）的 agent 工具目录——工具名、三档 tier（default 预选 / opt-in 显式开启 / forced 带激活条件）与参数。
- `save_agent_definition_draft` / `create_agent_definition`（#635）：#935 起 deprecated——不再写库，只返回引导：agent 节点的执行档案（`execution.runtime`、`tools`、`requires_labels`、`config_schema`、`skill`）写在 workflow 草稿的节点上，经 `save_workflow_draft` 保存；工具名保留到 P4 删除。

**Skill**

- `get_skill`：读 skill；可选 `ref=<tag>` 预览某个 git tag 的内容而不动 lock，非法 tag 返回 404 结构化错误。
- `validate_skill`：校验 SKILL.md + references/output-contract.md + scripts/validate_output.py 三件套，返回结构化错误清单。
- `save_skill_version`：作用于 skill root 下的 in-place 仓库，先校验路径与三件套再写文件，写完校验失败整体回滚，随后 commit + tag；tag 冲突 409。不动锁——pin 节点的重锁定仍是 operator 人操作（`make skills-lock`）；但这是生效写：`latest`（含空 ref）节点下次 dispatch 即跟随新 HEAD，不经任何发布。保存时会把 `_shared/map.json` 映射到该 skill 的材料以相同相对路径拷进仓库并计入 commit（响应的 `synced_files` 列出）；映射路径由共享副本权威裁决，payload 里手工携带映射路径会被 422 拒绝并列出冲突路径，应剔除后重试。
- `create_skill`（#633，workspace 作用域）：在 `~/.agents/skills/<workspace_id>/` 下新建 `<skill_name>` in-place 仓库。skill_name 必须匹配 `^[a-z0-9][a-z0-9_-]{0,63}$`，files 必须一次带齐三件套，目录已存在 409、未知 workspace 404；先校验后写盘，失败即删除半成品目录，可安全重试。初始 commit 以 agent-legion-studio 身份打 tag，同样不动锁、不发布。

**共享 skill 材料**（#633，workspace 作用域）

- `get_shared_materials`：读 workspace 的 `_shared`（map.json + references/ + scripts/）；无 `_shared` 时返回结构化空态 `{"map": null, "files": []}`。
- `save_shared_materials`：全量写 `_shared`。map.json 只是其中一个文件，由 agent 直接编写 JSON；先整体校验（路径只允许根下 `map.json` 与 `references/` / `scripts/` 下文件、map schema、逐文件上限）再落盘。`_shared` 不是 git 仓库，审计轨迹就是各 skill 仓库里被同步的 commit。
- `sync_shared_materials`（#673）：把映射材料主动传播进各 skill 仓库——拷入共享源、逐 skill commit 并打 +0.0.1 patch 新 tag（sources 省略即全部映射条目）；逐 skill 隔离返回 synced / skipped / failed + 新 tag，单 skill 失败不中断批次；只动本地 skill 仓库，DB skill lock 与节点 pin 不变——同样是生效写：`latest`（含空 ref）节点下次 dispatch 即取新 HEAD。

**预览面板**（#328）

- `get_preview_guide`：预览面板 playbook（本地静态资源，不发 HTTP）。
- `get_preview_context`：workspace 最近 job 的产物清单 + 单 job 的限幅内容采样，用来对齐真实数据形状。
- `get_preview_panel`：已发布 bundle + 待发布草稿，皆空即内置回落。
- `save_preview_panel_draft`：单文件 HTML bundle，校验非空 / 完整文档 / ≤256KiB，覆盖式草稿。没有发布工具——发布永远是人在 job detail 页「定制预览」对话框里的点击。

**Job 观测**（#329，全部只读）

- `get_job_context`：会话绑定的 job 上下文——job 详情 + 关注节点 + 该节点其它 job 的近期失败 + 建议动作 payload（仅绑定会话时注册）。
- `get_job_detail`：节点状态/错误、run 清单、产物名清单、声明的 inputs/outputs。
- `get_node_logs`：默认取最近失败 run，路径与密钥已消毒，尾截限幅。
- `read_artifact`：读产物内容，头截限幅。
- `list_jobs`：列最近 job，状态过滤 + limit ≤100。
- `compare_jobs`：对比两个 job，per-node 状态/错误并排 + newly_failed / recovered 摘要。

job 观测组没有任何生效工具：重跑类动作由 agent 引用 `suggested_actions` payload 输出建议，UI 渲染确认卡片，人确认后由宿主会话走常规 job 路由执行（scoped token 直接调动作端点被拒）。

**权限边界**：MCP server 只是薄转发，真正的约束在后端——scoped token 只能走工具面（草稿/校验/读取，外加上文 Skill 写工具对本地 skill 仓库的提交——它们对 `latest` 节点立即生效），workflow 发布、回滚、归档与预览面板发布等生效操作永远由人在 Studio 里完成（STUDIO-AGENT-001）。token 只存 sha256 digest，明文只在铸造时返回一次。Studio 对话内铸造的 run token（origin='run'）还绑定会话所在 workspace（schema v45，绑定与 token 行同一条 INSERT 原子写入）：带 workspace 路径的工具端点对其它 workspace 一律 403；自助 token（origin='user'，本文档流程铸造的）不带绑定，按 workspace 成员关系校验（成员/admin 放行，非成员 404）。注意铸造端点自 P4 起 admin-only——「自助」仅指 admin 用户自助，member 无法铸造新 token（已铸造未过期的 token 在 TTL 内仍可用）。

## 大文件的字节精确编辑（#767/#768）

`get_node_code`、`get_skill`、`get_shared_materials` 可以传
`output_path="snapshot.json"`，将完整响应直接导出到 MCP 主机的
`<进程 cwd>/data/studio-mcp-files/<workspace_id>/`，只返回路径、
字节数与 SHA-256。同机 agent 可本地解析、修改，再传 `code_path`
（节点源码）或 `files_path`（skill/shared 的 JSON 文件列表，亦接受
完整导出响应）提交；单个文件条目也支持 `file_path` 替代 `content`。
原来的 inline 参数保持兼容。不要同时提交路径参数和对应正文参数。

完整 skill 编辑导出仅限当前 workspace 自有的 skill；group skill 只能通过
不带 `output_path` 的 `get_skill` 读取原有公开内容，不能导出完整编辑快照。

skill/shared 的路径导出使用后端 `for_edit=true` 编辑快照，不能用展示
接口的投影代替。共享快照包含 `map.json` 原文及全部可写文件（不按扩展名
筛选）；损坏 UTF-8、超过可写上限或不安全的共享目录会整体拒绝导出，
避免重新保存时静默替换字节或删除遗漏文件。Python 构建残留（`__pycache__/`、
`*.pyc`）例外（#1038）：编辑导出按名跳过，全量保存把磁盘上的残留原样保留
（不视为被省略的文件），payload 携带残留路径返回 422；其余非 UTF-8 文件的
422 会提示删除或转换该二进制文件。`save_skill_version` 检查未提交改动时
忽略未暂存的构建残留，提交只含本次声明的文件。

路径只允许该 workspace 暂存目录内的普通 UTF-8 文件，禁止越界与链接。
导出不会覆盖已有文件，读取后仍走原来的权限和内容校验；不会直接发布。
Skill 是增量更新，导出后只保留要提交的非共享映射文件；映射文件应通过
共享材料工具修改其来源。共享材料仍需提交完整文件集，skill 已存在的 tag 仍返回冲突。远程 agent
若无法访问 MCP 主机文件系统，应继续使用 inline 参数。完整操作顺序见
`get_authoring_guide(section="tool-map")`。

## 1. 铸造 token

token 是 **admin 用户**签发的长效 scoped token（P4 起 `POST /api/studio-agent-tokens` 及 list/DELETE 端点全部 admin-only，非 admin 403；origin='user'，默认 168h，上限 720h）：

> 下文示例 base URL 默认写 `http://127.0.0.1:8000`；若使用 dev 栈，后端默认端口为 `8001`（prod 为 `8000`），请按需替换。

```bash
# 登录拿 session cookie
curl -c /tmp/al-cookies.txt -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "<用户名>", "password": "<密码>"}'

# 铸造（明文 token 只在这一次响应里出现，立即收好）
curl -b /tmp/al-cookies.txt -X POST http://127.0.0.1:8000/api/studio-agent-tokens \
  -H 'Content-Type: application/json' -H 'x-agent-legion-request: 1' \
  -d '{"ttl_hours": 168}'
# -> {"id": "...", "token": "<明文>", "expires_at": "..."}

# 列出自己的 token（只有 id/时间戳，绝无明文或 digest）
curl -b /tmp/al-cookies.txt http://127.0.0.1:8000/api/studio-agent-tokens

# 吊销
curl -b /tmp/al-cookies.txt -X DELETE \
  -H 'x-agent-legion-request: 1' \
  http://127.0.0.1:8000/api/studio-agent-tokens/<id>
```

## 2. 配置 MCP 客户端

> Studio 对话（Workflow Studio 聊天面板）不走这条 stdio 入口：kimi ≥ 0.38 的
> ACP `session/new` 只接受 `type: "http" | "sse"` 的 MCP server，因此对话会话改由
> 后端内嵌的 streamable-HTTP 端点 `/api/studio-agent/mcp` 承载（scoped token 走
> `Authorization: Bearer` header、会话绑定走 `x-agent-legion-mcp-session-id`
> header，随 `session/new` 自动注入，无需任何手工配置）。本节的 stdio 配置只面向
> 自助接入的外部 agent。

服务入口：`uv run python -m server.app.mcp_server`（在仓库根目录下运行）。三个环境变量：

- `AGENT_LEGION_STUDIO_AGENT_TOKEN`（必填，缺失即启动失败）
- `AGENT_LEGION_MCP_API_BASE`（可选，默认 `http://127.0.0.1:8000`）
- `AGENT_LEGION_MCP_SESSION_ID`（可选；自助配置不设置即可——不设置时 `get_studio_context` / `get_job_context` 两个会话绑定工具不注册，工具面为 33 个，#660）

### Kimi Code

写进项目级 `.kimi-code/mcp.json`（或用户级 `~/.kimi-code/mcp.json`），见
[Kimi Code MCP 文档](https://www.kimi.com/code/docs/en/kimi-code-cli/customization/mcp.html)：

```json
{
  "mcpServers": {
    "agent-legion-studio": {
      "command": "uv",
      "args": ["run", "python", "-m", "server.app.mcp_server"],
      "cwd": "/path/to/agent-legion",
      "env": {
        "AGENT_LEGION_MCP_API_BASE": "http://127.0.0.1:8000",
        "AGENT_LEGION_STUDIO_AGENT_TOKEN": "<上一步铸造的明文 token>"
      }
    }
  }
}
```

### Claude Code

项目根 `.mcp.json`（或 `claude mcp add-json`），格式相同：

```json
{
  "mcpServers": {
    "agent-legion-studio": {
      "command": "uv",
      "args": ["run", "python", "-m", "server.app.mcp_server"],
      "cwd": "/path/to/agent-legion",
      "env": {
        "AGENT_LEGION_MCP_API_BASE": "http://127.0.0.1:8000",
        "AGENT_LEGION_STUDIO_AGENT_TOKEN": "<上一步铸造的明文 token>"
      }
    }
  }
}
```

配置后新 session 里会出现 33 个 `mcp__agent-legion-studio__*` 工具（无会话绑定的外部接入；Studio 对话内为 35 个）。
- token 泄露处置：`DELETE /api/studio-agent-tokens/<id>` 吊销即可，即刻生效。
- token 到期或吊销后 MCP 调用返回 `HTTP 401: ...` 文本，按第 1 节重新铸造并更新配置。
- 依赖钉在 `mcp>=1.12,<2`：mcp 2.0 移除了 `mcp.server.fastmcp`，独立 fastmcp 3.x 与之不兼容（见 pyproject.toml 注释）。
