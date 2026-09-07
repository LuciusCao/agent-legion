"""pi runtime adapter：pi argv 构建（自 workflows/pi_protocol 迁入，issue #75）。

argv 与原实现逐字节一致；prompt 指令由调用方注入，避免本模块反向 import
pi_protocol 形成 import 环。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.agent_runtime.adapter import ExecutionContract, ExecutionKeyRule, RuntimeAdapter
from server.app.agent_runtime.tool_catalog import PI_TOOL_CATALOG


def build_command(
    manifest: dict[str, Any],
    *,
    skill_dir: Path,
    session_dir: Path,
    session_name: str,
    prompt_file: Path,
    prompt_instruction: str,
) -> list[str]:
    """Build the pi CLI argv for one workflow node run."""
    execution = manifest["execution"]
    cmd: list[str] = [
        str(execution.get("binary") or "pi"),
        "--mode",
        "json",
        "--session-dir",
        str(session_dir),
        "--name",
        session_name,
        "--no-context-files",
        "--no-extensions",
        "--no-prompt-templates",
        "--no-skills",
        "--skill",
        str(skill_dir),
        "--tools",
        ",".join(manifest["tools"]),
        "--approve",
    ]
    for flag, key in (("--provider", "provider"), ("--model", "model"), ("--thinking", "thinking")):
        value = str(execution.get(key) or "")
        if value:
            cmd.extend([flag, value])
    cmd.extend([f"@{prompt_file}", prompt_instruction])
    return cmd


ADAPTER = RuntimeAdapter(
    name="pi",
    binary="pi",
    build_command=build_command,
    execution=ExecutionContract(
        keys={
            "provider": ExecutionKeyRule(True, "平台连接选择器 → --provider"),
            "model": ExecutionKeyRule(True, "模型 id → --model"),
            "thinking": ExecutionKeyRule(False, "思考档位 → --thinking（空 = runtime 决定）"),
        }
    ),
    # #476：外部 runtime 无自描述通道，按实测静态登记（同名三件套只是
    # 命名巧合，语义由 pi 自身决定）。
    tool_catalog=PI_TOOL_CATALOG,
)
