"""Per-call output truncation attribution for agent event streams (#952).

单次输出触顶（provider 侧 ``max_tokens`` / ``finish_reason=length``）在 pi 与
velites 的事件流里统一表现为 assistant ``message_end`` 的 ``stopReason=length``。
触顶本身不是失败——velites 把它当普通停止继续收尾，触顶后产物仍可能齐全——
所以它不进 model_error（``shared/pi_model_error.py``，exit 0 时直接判 run 失败），
只在声明产物缺失时作为失败归因，把笼统的「Missing outputs」换成可行动的触顶
原因。Stdlib-only（见 ``shared/__init__.py``）：Worker 归因、Host 失败分类与
日志渲染共用这里的常量。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

OUTPUT_LIMIT_STOP_REASON = "length"
OUTPUT_TRUNCATED_PREFIX = "Model output hit the per-call output token limit"
_MAX_LISTED_MISSING = 10
# 只有这两种退出码下「产物缺失」才是 run 的失败面：0 = pi（Host 判缺产物），
# 1 = velites 产物契约退出；崩溃 / 超时 / 取消保持各自归因。
_ATTRIBUTABLE_EXIT_CODES = (0, 1)


@dataclass
class OutputTruncation:
    """单遍扫描中累计的输出触顶次数；``observe`` 作为扫描的事件观察者。"""

    count: int = 0

    def observe(self, event: dict[str, Any]) -> None:
        # 只看 message_end：message_start/turn_end 也携带同一条消息，重复计数。
        if event.get("type") != "message_end":
            return
        msg = event.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            return
        if msg.get("stopReason") == OUTPUT_LIMIT_STOP_REASON:
            self.count += 1

    def failure(self, expected: Sequence[str], produced: Sequence[str], exit_code: int) -> str:
        """归因判定：触顶过、声明产物有缺失且退出码可归因时返回失败原因，否则 ""。

        只改写失败原因、不改成败：产物齐全的触顶 run 照常完成。"""
        missing = [name for name in expected if name not in produced]
        if not self.count or not missing or exit_code not in _ATTRIBUTABLE_EXIT_CODES:
            return ""
        return output_truncation_error(self.count, missing)


def output_truncation_error(count: int, missing: Sequence[str]) -> str:
    """触顶 + 产物缺失时的失败原因：点名触顶次数、缺失产物与可行动的配平手段。"""
    shown = ", ".join(missing[:_MAX_LISTED_MISSING])
    if len(missing) > _MAX_LISTED_MISSING:
        shown += f" (+{len(missing) - _MAX_LISTED_MISSING} more)"
    return (
        f"{OUTPUT_TRUNCATED_PREFIX} (stopReason=length, {count}x) and declared outputs "
        f"are missing: {shown}. Thinking shares the same per-call output budget: lower "
        "execution.thinking, write the output in smaller chunks across several tool "
        "calls, or raise the node config max_output_tokens (velites)"
    )
