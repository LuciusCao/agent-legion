"""openclaw runtime adapter（issue #75 阶段 3）。

实测结论（本机 OpenClaw 2026.6.11，CLI --help / dist 源码 / 官方
docs/cli/agent.md 三方核验）：``openclaw agent --json`` 的 stdout 是
**一次性结果 envelope**（pretty-printed JSON：
``{payloads: [{text, mediaUrl?}], meta: {...}}``），诊断走 stderr——不是
pi 兼容的流式 JSONL 事件流，也没有中间事件可翻译。事件侧由 Worker 在
进程退出后把 envelope 合成为 pi 子集事件（``worker/openclaw_events.py``，
单文件）。真实模型调用本机未跑通（--local 与 Gateway 均 401
invalid_authentication_error），argv 按 CLI 文档核验、事件形状按
dist 实现与官方文档核验。

argv 选择：`--local` 嵌入式一次性 run（Worker 无 Gateway 依赖；Gateway
模式还会话归属/abort 语义都依赖常驻进程）。pi/velites 专属概念不强行
映射：skill_dir/session_dir/prompt_instruction 不用（prompt 全文经
--message-file 下发），expected_outputs 由 Host 侧产物校验兜底。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.agent_runtime.adapter import ExecutionContract, ExecutionKeyRule, RuntimeAdapter


def build_command(
    manifest: dict[str, Any],
    *,
    skill_dir: Path,
    session_dir: Path,
    session_name: str,
    prompt_file: Path,
    prompt_instruction: str,
) -> list[str]:
    """Build the openclaw CLI argv for one workflow node run.

    provider 存在时拼成 ``provider/model`` 组合串进 ``--model``（契约
    semantics）；--session-id 提供 CLI 要求的会话选择器。
    """
    execution = manifest["execution"]
    model = str(execution.get("model") or "")
    provider = str(execution.get("provider") or "")
    if provider:
        model = f"{provider}/{model}"
    cmd: list[str] = [
        str(execution.get("binary") or "openclaw"),
        "agent",
        "--local",
        "--json",
        "--model",
        model,
        "--session-id",
        session_name,
        "--message-file",
        str(prompt_file),
    ]
    thinking = str(execution.get("thinking") or "")
    if thinking:
        cmd.extend(["--thinking", thinking])
    timeout = execution.get("timeout_seconds")
    if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout > 0:
        cmd.extend(["--timeout", str(timeout)])
    return cmd


ADAPTER = RuntimeAdapter(
    name="openclaw",
    binary="openclaw",
    build_command=build_command,
    execution=ExecutionContract(
        keys={
            "provider": ExecutionKeyRule(False, "可选；拼成 provider/model 组合串进 --model"),
            "model": ExecutionKeyRule(True, "模型 id（或与 provider 的组合串）→ --model"),
            "thinking": ExecutionKeyRule(False, "思考档位 → --thinking（空 = runtime 决定）"),
        }
    ),
)
