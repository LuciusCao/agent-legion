"""Optional, tolerated-absent Agent Worker result-metadata extras.

Split out of ``agent_worker_results.py`` so the route module stays within its
size budget: the five optional record keys that older/newer Workers may omit
— the #748 agent-crash stderr tail, the #748 R2 / #755 writer-side artifact
manifest truncation markers (v1 legacy), and the #865 output-truncation
facts. None of them ever rejects the report (a 400 would drop the result and
requeue the whole agent execution); every shape anomaly is normalized
defensively instead.
"""

from __future__ import annotations

from typing import Any

from shared.code_contract import RESULT_OUTPUT_ARTIFACTS_FLAG

# #748: optional agent-crash stderr tail the Worker appends to the result
# metadata; capped at the error_message budget (the Worker truncates to the
# same bound, the reader re-truncates defensively for older/other writers).
_MAX_AGENT_STDERR_TAIL_CHARS = 4000
# #748 R2 P2-1 + #755 对抗复审 P2-1（v1 legacy 写侧，worker-v0.7.18 及更早）:
# the old Worker's X-Agent-Result byte budget (h11 caps one HTTP event at
# 16 KiB) could force a 128-entry artifact manifest past the limit; its
# degrade dispatched on the ref FORM: direct-upload dict refs moved the full
# manifest into the result archive as the reserved member
# ``result-output-artifacts.json`` (see ARTIFACTS_IN_ARCHIVE_KEY below — a
# kept prefix would flip the run to "Missing outputs"); CAS string refs
# (bytes already IN the archive) took the last-resort truncation to an empty
# list and stamped these markers. #843 PR-2 起新 Worker（v2 result.json）不再
# 产生任何标记/换轨成员；Host 读侧保留旧 Worker 兼容窗。The markers are
# part of the completion contract: with
# ``output_artifacts_truncated`` set, the completion handler skips the
# empty-manifest completed→failed flip and judges produced/missing from
# the staged archive view. Both keys are optional and tolerated-absent
# like agent_stderr_tail above (older Workers / non-truncating shapes).
ARTIFACTS_TRUNCATED_KEY = "output_artifacts_truncated"
ARTIFACTS_TOTAL_KEY = "output_artifacts_total"
# #755 codex P1（v1 legacy 写侧）：结果头溢出（直传 dict ref 清单撞破头
# 预算）的换轨标记——旧 Worker 把完整 direct-ref 清单写进结果归档成员
# ``result-output-artifacts.json``，头里只带本布尔；commit 层从归档读回
# 清单并 enrich outcome（agent_broker/result_output_manifest.py +
# agent_result_commit.py），产物字节不重复传输（已在 S3）。#843 PR-2 起
# 新 Worker 不再写它（v2 契约明文禁发，读侧 v2 形态显式剥离）；Host 保留
# 旧 Worker 兼容窗。可选、容忍缺席，与上方截断标记同纪律。键名的单一
# 事实来源在 shared/code_contract.py，此处仅作本地别名。
ARTIFACTS_IN_ARCHIVE_KEY = RESULT_OUTPUT_ARTIFACTS_FLAG


def _truncation_facts(raw: Any) -> dict[str, Any] | None:
    """#865：Worker 上报 ``output_truncation`` 键的防御归一——可选、容忍缺席
    （旧 Worker 不带该键）；形态错误永不拒收（400 会丢结果）：非 dict 丢弃，
    ``count`` 非非负 int 归零（bool 不是 int），``last_stop`` 非 str 归 None、
    超长截断（stopReason wire 名是短枚举串）。"""
    if not isinstance(raw, dict):
        return None
    count = raw.get("count", 0)
    last_stop = raw.get("last_stop")
    return {
        "count": count if type(count) is int and count >= 0 else 0,
        "last_stop": str(last_stop)[:32] if isinstance(last_stop, str) else None,
    }


def parse_result_extras(metadata: dict[str, Any]) -> dict[str, Any]:
    """Normalize the optional extras into their record shape (stable keys,
    absence/defensive defaults for older Workers and malformed values)."""
    # #748: bounded, optional stderr tail for agent-crash attribution
    # (absent for completed/cancelled runs and older Workers). Keep the END
    # on the defensive cap too (#755 终审 P2-2): the tail exists because the
    # crash stack sits at the end of the stream.
    agent_stderr_tail = str(metadata.get("agent_stderr_tail", ""))[-_MAX_AGENT_STDERR_TAIL_CHARS:]
    # #748 R2 P2-1: writer-side artifact-list truncation markers. The Worker
    # only emits them when the byte budget forced a degrade, and then
    # ALWAYS as a pair; tolerate a lone/missing half the same way (absent =
    # full list, non-int total = treat as truncated with unknown origin).
    artifacts_truncated = metadata.get(ARTIFACTS_TRUNCATED_KEY) is True
    artifacts_total_raw = metadata.get(ARTIFACTS_TOTAL_KEY, 0)
    artifacts_total = artifacts_total_raw if type(artifacts_total_raw) is int else 0
    # #755 codex P1：清单走归档成员的标记——is True 归一，进 record 审计面；
    # AgentOutcome 不加字段（commit 层直接从 record 读标记、从归档读清单）。
    artifacts_in_archive = metadata.get(ARTIFACTS_IN_ARCHIVE_KEY) is True
    return {
        "agent_stderr_tail": agent_stderr_tail,
        ARTIFACTS_TRUNCATED_KEY: artifacts_truncated,
        ARTIFACTS_TOTAL_KEY: artifacts_total,
        ARTIFACTS_IN_ARCHIVE_KEY: artifacts_in_archive,
        # #865: per-call output-truncation facts ({count, last_stop}); None
        # for older Workers / untruncated runs (stable shape).
        "output_truncation": _truncation_facts(metadata.get("output_truncation")),
    }
