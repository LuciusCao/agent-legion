"""MCP 工具名清单契约测试（issue #678：根治手工同步漂移）。

三边全等：create_mcp_server 实际注册的工具名 == tool_names.py 权威清单 ==
studio_chat/prompts.py 引用的清单。#678 之前 prompts.py 手抄清单与注册面
脱节 12 个工具，looks_like_agent_legion_tool_call 不认识它们，权限自动批准
静默降级为人工确认；此后任何一边改名/增删都会在这里炸出来。
"""

from __future__ import annotations

import asyncio

import pytest

from server.app.mcp_server.config import McpServerConfig
from server.app.mcp_server.server import create_mcp_server
from server.app.mcp_server.tool_names import AGENT_LEGION_MCP_TOOL_NAMES
from server.app.studio_chat.prompts import (
    AGENT_LEGION_MCP_TOOL_NAMES as PROMPTS_TOOL_NAMES,
)
from server.app.studio_chat.prompts import looks_like_agent_legion_tool_call

pytestmark = pytest.mark.no_db

_CONFIG = McpServerConfig(
    api_base="http://backend.test:9000",
    token="scoped-token-1",
    # get_job_context 只在 session-bound 形态注册（#660）；静态外部
    # config 不注册它。契约测试用 session 绑定形态比对完整清单——
    # 与生产（HTTP transport 恒 session_bound=True）一致。
    session_id="session-contract-test",
)

# #678 手抄清单欠收的 12 个工具——直接回归面：当时它们全部绕过了权限
# 自动批准。缺一个即说明清单又与注册面脱节（三边全等测试会先炸，这里
# 钉住该 issue 修复的行为语义）。
_TOOLS_MISSED_BY_THE_DRIFTED_COPY = {
    "compare_jobs",
    "get_job_context",
    "get_job_detail",
    "get_node_logs",
    "get_preview_context",
    "get_preview_guide",
    "get_preview_panel",
    "get_publish_request_status",
    "list_jobs",
    "read_artifact",
    "request_workflow_publish",
    "save_preview_panel_draft",
}


def _registered_tool_names() -> set[str]:
    # 构建不发起任何 HTTP 调用（工具体只在被调用时才碰 ToolClient），
    # 无需 mock httpx；list_tools 是 FastMCP 的公开 async API（1.x 无同步
    # 等价物，注册后断言因此留在测试侧而非 create_mcp_server 里）。
    server = create_mcp_server(_CONFIG)
    return {tool.name for tool in asyncio.run(server.list_tools())}


def test_registered_tools_match_the_manifest() -> None:
    registered = _registered_tool_names()
    manifest = set(AGENT_LEGION_MCP_TOOL_NAMES)
    assert registered == manifest, (
        "FastMCP 注册面与 tool_names.py 清单脱节："
        f"注册未收录 {sorted(registered - manifest)}，"
        f"清单虚列 {sorted(manifest - registered)}"
    )


def test_prompts_side_references_the_same_manifest() -> None:
    # prompts.py 直接 import 权威常量（#678）；本测试防任何人把它换回
    # 手抄清单——import 失败（别名被删）或集合不等都在这里失守。
    assert set(PROMPTS_TOOL_NAMES) == set(AGENT_LEGION_MCP_TOOL_NAMES)


def test_manifest_covers_the_tools_missed_by_the_drifted_copy() -> None:
    assert set(AGENT_LEGION_MCP_TOOL_NAMES) >= _TOOLS_MISSED_BY_THE_DRIFTED_COPY


def test_every_manifest_tool_is_a_recognized_call_identity() -> None:
    # looks_like 的三种身份字段形态都必须命中：裸工具名（kind/title 的
    # 整字段恰好是工具名时）、name(...) 调用形态、server__name 前缀——
    # 权限自动批准与 mcp_status 烟雾信号都依赖这条识别路径。
    for name in sorted(AGENT_LEGION_MCP_TOOL_NAMES):
        assert looks_like_agent_legion_tool_call(name), name
        assert looks_like_agent_legion_tool_call(f"agent-legion-studio__{name}"), name
        assert looks_like_agent_legion_tool_call(f"{name}(workspace_id=ws-1)"), name
