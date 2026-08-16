# Studio Agent MCP Server

把平台的 studio-agent 工具面（`/api/studio-agent/tools/*`）以 MCP stdio server 的形式暴露给任意外部 agent（Kimi Code、Claude Code 等）。外部 agent 拿到 9 个工具：读会话上下文（`get_studio_context`，仅 Studio 对话内绑定会话时可用）、列 workflow 目录、读激活 revision、校验/对比草稿、存节点代码草稿、存 Agent 定义草稿、注册 workflow key。

**权限边界**：MCP server 只是薄转发，真正的约束在后端——scoped token 只能走工具面（草稿/校验/注册/读取），发布、回滚、归档等生效操作永远由人在 Studio 里完成（STUDIO-AGENT-001）。其中注册 workflow key 是平台全局动作，与人类侧 `POST /api/workflows` 的 `require_admin` 对齐：只有 admin 用户铸造的 scoped token 能注册，非 admin 一律 403。token 只存 sha256 digest，明文只在铸造时返回一次。Studio 对话内铸造的 run token（origin='run'）还绑定会话所在 workspace（schema v45，绑定与 token 行同一条 INSERT 原子写入）：带 workspace 路径的工具端点对其它 workspace 一律 403；自助 token（origin='user'，本文档流程铸造的）不带绑定，按 workspace 成员关系校验（成员/admin 放行，非成员 404）。

## 1. 铸造 token

token 是用户自助签发的长效 scoped token（origin='user'，默认 168h，上限 720h）：

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

服务入口：`uv run python -m server.app.mcp_server`（在仓库根目录下运行）。三个环境变量：

- `AGENT_LEGION_STUDIO_AGENT_TOKEN`（必填，缺失即启动失败）
- `AGENT_LEGION_MCP_API_BASE`（可选，默认 `http://127.0.0.1:8000`）
- `AGENT_LEGION_MCP_SESSION_ID`（可选，Studio 对话场景由后端自动注入，支撑 `get_studio_context` 工具；自助配置不设置即可，该工具会返回未绑定提示）

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

配置后新 session 里会出现 9 个 `mcp__agent-legion-studio__*` 工具。

## 3. 注意

- token 泄露处置：`DELETE /api/studio-agent-tokens/<id>` 吊销即可，即刻生效。
- token 到期或吊销后 MCP 调用返回 `HTTP 401: ...` 文本，按第 1 节重新铸造并更新配置。
- 依赖钉在 `mcp>=1.12,<2`：mcp 2.0 移除了 `mcp.server.fastmcp`，独立 fastmcp 3.x 与之不兼容（见 pyproject.toml 注释）。
