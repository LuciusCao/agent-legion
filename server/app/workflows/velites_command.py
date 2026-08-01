"""velites flavor 的 headless 命令构建（docs/architecture/velites-harness.md §6/§9）。

与 pi flavor 的 argv 差异（velites CLI 见 ``velites/src/cli.rs``，未知 flag 硬报错）：

- 去掉 pi 专属 flag（``--no-context-files`` / ``--no-extensions`` /
  ``--no-prompt-templates`` / ``--no-skills`` / ``--approve``）：velites 零自动发现，
  上下文来源仅 system-prompt/skill/prompt 三者，无对应概念；
- 新增 ``--require-output``：manifest ``expected_outputs`` 逐条下发，驱动 harness
  输出自检与 ``outputs_validation`` 事件；
- 预算 flag ``--max-turns`` / ``--max-tokens`` 取自节点可调参数解析链
  （config_schema defaults → 节点 config → workspace 覆盖，intake 冻结后经
  ``manifest["config"]`` 下发，CONFIG-MANIFEST-001 白名单内）；节点未配置则不发，
  这里不硬编码默认值；
- ``--timeout-seconds`` 与 capability timeout（``pi_runner`` 的外层 kill 时限）取同一值：
  harness 内层 deadline 到期先给模型一个 wrap-up turn 收尾，Host SIGTERM 兜底，
  velites 对 SIGTERM 优雅退出（``agent_end{reason: cancelled}`` + exit 0）。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

FLAVOR_PI = "pi"
FLAVOR_VELITES = "velites"

# 节点 config 中 velites 预算的保留键名（capability config_schema 声明后才可能出现在
# manifest["config"] 里；未声明的键在解析链上就被拒绝）。
MAX_TURNS_KEY = "max_turns"
MAX_TOKENS_KEY = "max_tokens"


def build_command_for_flavor(
    manifest: dict[str, Any],
    *,
    skill_dir: Path,
    session_dir: Path,
    session_name: str,
    prompt_file: Path,
    prompt_instruction: str,
    pi_fallback: Callable[..., list[str]],
) -> list[str]:
    """按 ``manifest["pi"]["flavor"]`` 分发命令构建；未知 flavor fail-fast。

    ``prompt_instruction`` / ``pi_fallback`` 由调用方（pi_protocol 侧）注入：
    本模块不得反向 import pi_protocol（架构契约禁 import 环，函数级也算）。
    """
    kwargs: dict[str, Any] = {
        "skill_dir": skill_dir,
        "session_dir": session_dir,
        "session_name": session_name,
        "prompt_file": prompt_file,
    }
    flavor = str(manifest["pi"].get("flavor") or FLAVOR_PI).strip()
    if flavor == FLAVOR_VELITES:
        return build_velites_command(manifest, prompt_instruction=prompt_instruction, **kwargs)
    if flavor == FLAVOR_PI:
        return pi_fallback(manifest, **kwargs)
    raise ValueError(f"unknown pi flavor {flavor!r} (expected 'pi' or 'velites')")


def build_velites_command(
    manifest: dict[str, Any],
    *,
    skill_dir: Path,
    session_dir: Path,
    session_name: str,
    prompt_file: Path,
    prompt_instruction: str,
) -> list[str]:
    """Build the velites CLI argv for one workflow node run."""
    pi = manifest["pi"]
    cmd: list[str] = [
        str(pi.get("binary") or FLAVOR_VELITES),
        "--mode",
        "json",
        "--session-dir",
        str(session_dir),
        "--name",
        session_name,
        "--skill",
        str(skill_dir),
        "--tools",
        ",".join(manifest["tools"]),
    ]
    for flag, key in (("--provider", "provider"), ("--model", "model"), ("--thinking", "thinking")):
        value = str(pi.get(key) or "")
        if value:
            cmd.extend([flag, value])
    node_config = manifest.get("config")
    if isinstance(node_config, dict):
        for flag, key in (("--max-turns", MAX_TURNS_KEY), ("--max-tokens", MAX_TOKENS_KEY)):
            budget = node_config.get(key)
            if isinstance(budget, int) and not isinstance(budget, bool) and budget > 0:
                cmd.extend([flag, str(budget)])
    timeout = pi.get("timeout_seconds")
    if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout > 0:
        cmd.extend(["--timeout-seconds", str(timeout)])
    if pi.get("velites_no_sandbox"):
        cmd.append("--no-sandbox")
    for output in manifest.get("expected_outputs") or []:
        cmd.extend(["--require-output", str(output)])
    cmd.extend([f"@{prompt_file}", prompt_instruction])
    return cmd
