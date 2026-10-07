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

from shared.pi_model_error import fold_model_error

OUTPUT_LIMIT_STOP_REASON = "length"
OUTPUT_TRUNCATED_PREFIX = "Model output hit the per-call output token limit"
_MAX_LISTED_MISSING = 10
_BUDGET_EXCEEDED = "budget_exceeded"
# velites 契约引擎对「声明文件不存在」的违例文案（``velites/src/contract.rs``，
# 渲染为 ``<path>: missing required file``）：它只是 ``missing`` 的契约模式复述，
# 本身可由触顶解释；其余违例（契约解析失败、内容/schema 规则）是确定性问题。
_MISSING_FILE_VIOLATION = ": missing required file"


@dataclass
class OutputTruncation:
    """单遍扫描中累计的触顶事实；``observe`` 作为扫描的事件观察者。

    归因只看**最后一次** assistant 模型调用（``last_stop``，即导致最终产物
    校验 / 退出的那一轮）：velites 的补救轮或 pi 的后续轮以 ``stop`` /
    ``toolUse`` 正常结束、或以模型错误结束时，更早的触顶不再解释最终缺产物；
    ``count`` 只是文案里的累计次数。另记排除归因的旁证：未恢复的模型调用错误
    （与扫描的 model_error 同一 fold，不受 exit 0 门控）、velites 预算耗尽
    （``agent_end.reason``）、``outputs_validation`` 是否出现（证明 velites 的
    exit 1 来自产物契约）及其 ``violations`` 里缺文件之外的契约违例。完整决策表
    见 ``docs/architecture/llm-output-budget-design.md``「触顶归因决策表」。"""

    count: int = 0
    last_stop: Any = None
    model_error: str | None = None
    budget_exceeded: bool = False
    outputs_validated: bool = False
    contract_violation: bool = False

    def observe(self, event: dict[str, Any]) -> None:
        self.model_error = fold_model_error(event, self.model_error)
        kind = event.get("type")
        if kind == "outputs_validation":
            self.outputs_validated = True
            violations = event.get("violations")
            self.contract_violation = isinstance(violations, list) and any(
                not (isinstance(v, str) and v.endswith(_MISSING_FILE_VIOLATION)) for v in violations
            )
        elif kind == "agent_end":
            self.budget_exceeded |= event.get("reason") == _BUDGET_EXCEEDED
        elif kind == "message_end":
            # 只看 message_end：message_start/turn_end 也携带同一条消息，重复计数。
            msg = event.get("message")
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                self.last_stop = msg.get("stopReason")
                self.count += self.last_stop == OUTPUT_LIMIT_STOP_REASON

    def failure(self, expected: Sequence[str], produced: Sequence[str], exit_code: int) -> str:
        """归因判定：最后一次模型调用触顶、声明产物缺失且缺失确由触顶解释时返回
        失败原因，否则 ""。

        只改写失败原因、不改成败：产物齐全的触顶 run 照常完成。可归因的退出面
        只有两个：exit 0（pi 正常退出、Host 判缺产物）与带 ``outputs_validation``
        的 exit 1（velites 产物契约退出）；pi 的 exit 1 是进程失败，不归因。未恢复
        的模型错误、预算耗尽、缺文件之外的契约违例（如 skill contract 无法解析）
        是更直接的原因，一律不改写。"""
        missing = [name for name in expected if name not in produced]
        contract_exit = exit_code == 0 or (exit_code == 1 and self.outputs_validated)
        if self.last_stop != OUTPUT_LIMIT_STOP_REASON or not missing or not contract_exit:
            return ""
        if self.model_error or self.budget_exceeded or self.contract_violation:
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
        "calls, or raise the node config max_output_tokens (velites). A provider may "
        "also report a context-window overflow as stopReason=length; if the input is "
        "already near the model's context window, shrink the context instead"
    )
