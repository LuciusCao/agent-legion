"""Worker 结果准备的单次输出触顶归因（#952）。

触顶（assistant ``stopReason=length``）只在**最后一次**模型调用触顶且声明产物
缺失时改写失败原因，不改变任何 run 的成败判定。``_DECISION_TABLE`` 与
docs/architecture/llm-output-budget-design.md「触顶归因决策表」逐行对应。
共享桩见 tests/workers/upload_queue_testlib.py。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.output_truncation import (
    OUTPUT_TRUNCATED_PREFIX,
    OutputTruncation,
    output_truncation_error,
)
from shared.pi_events import scan_and_compress_pi_events
from shared.pi_model_error import fold_model_error
from tests.workers.upload_queue_testlib import QueueFakeClient, _execution_dir, _queue, _task

# velites 产物契约的证据事件：exit 1 只有带它才可归因为触顶。
_VALIDATION = {"type": "outputs_validation", "missing": ["output.json"]}
_END = {"type": "agent_end"}


def _contract_validation(*violations: str) -> dict:
    """velites 契约模式（#443）的 outputs_validation，``violations`` 为渲染后的
    ``<path>: <message>``（velites/src/events.rs OutputsValidationEvent）。"""
    return {
        "type": "outputs_validation",
        "missing": ["output.json"],
        "mode": "contract",
        "violations": list(violations),
    }


def _assistant(stop_reason: str, event_type: str = "message_end") -> dict:
    return {"type": event_type, "message": {"role": "assistant", "stopReason": stop_reason}}


def _write_events(work_root: Path, events: list[dict], stderr: tuple[str, ...] = ()) -> None:
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    lines = [*stderr, *(json.dumps(event) for event in events)]
    (run_dir / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _report(work_root: Path, *, exit_code: int) -> dict:
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=exit_code))
    queue.shutdown()
    return client.reports[0]


def _drop_output(work_root: Path) -> None:
    (work_root / "exec-1" / "job" / "output.json").unlink()


def test_observer_counts_only_assistant_message_end(tmp_path: Path) -> None:
    """计数只看 assistant 的 message_end：message_start / turn_end 携带同一条
    消息不重复计数，非 length 停止与非 assistant 角色不计。"""
    events = tmp_path / "events.jsonl"
    stream = [
        _assistant("length", "message_start"),
        _assistant("length"),
        _assistant("length", "turn_end"),
        _assistant("toolUse"),
        {"type": "message_end", "message": {"role": "user", "stopReason": "length"}},
        _assistant("length"),
    ]
    events.write_text("\n".join(json.dumps(e) for e in stream) + "\n", encoding="utf-8")
    truncation = OutputTruncation()
    model_error, _, _, _ = scan_and_compress_pi_events(events, event_observer=truncation.observe)
    assert truncation.count == 2
    assert truncation.last_stop == "length"
    # 触顶不进 model_error（exit 0 时 model_error 直接判失败）。
    assert model_error is None


def test_truncation_error_caps_listed_missing_outputs() -> None:
    message = output_truncation_error(3, [f"out/{i}.json" for i in range(12)])
    assert message.startswith(OUTPUT_TRUNCATED_PREFIX)
    assert "3x" in message
    assert "out/9.json" in message and "out/10.json" not in message
    assert "(+2 more)" in message


def test_truncation_message_mentions_context_window() -> None:
    """Anthropic 的 model_context_window_exceeded 同样映射为 length，文案需覆盖。"""
    assert "context window" in output_truncation_error(1, ["a.json"])


def _error(message: str) -> dict:
    return {
        "type": "message_end",
        "message": {"role": "assistant", "stopReason": "error", "errorMessage": message},
    }


# 瞬态失败的一次重试（pi / velites 同形：error message_end + auto_retry_start）。
_RETRY = [
    _error("429 rate limited"),
    {"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3, "delayMs": 10, "error": "429"},
]
_LENGTH_WITH_ERROR = {
    "type": "message_end",
    "message": {"role": "assistant", "stopReason": "length", "errorMessage": "boom"},
}
_TRUNCATED = "truncated"
_EXITED_1 = "Agent process exited 1: velites: boom"

# (id, exit_code, events, 产物齐全, 期望 error_message；_TRUNCATED 表示触顶归因，
# "" 表示 completed)。行号与设计文档决策表一致。
_DECISION_TABLE = [
    ("1-pi-stop-complete", 0, [_assistant("stop")], True, ""),
    ("2-pi-stop-missing", 0, [_assistant("stop")], False, ""),
    ("3-pi-length-complete", 0, [_assistant("length")], True, ""),
    ("4-pi-length-missing", 0, [_assistant("toolUse"), _assistant("length")], False, _TRUNCATED),
    (
        "5-pi-length-then-stop",
        0,
        [_assistant("length"), _assistant("toolUse"), _assistant("stop")],
        False,
        "",
    ),
    ("6-pi-retry-into-length-missing", 0, [*_RETRY, _assistant("length")], False, _TRUNCATED),
    ("7-pi-retry-into-length-complete", 0, [*_RETRY, _assistant("length")], True, ""),
    (
        "8-pi-length-retry-into-stop",
        0,
        [_assistant("length"), *_RETRY, _assistant("stop")],
        False,
        "",
    ),
    ("9-pi-length-then-model-error", 0, [_assistant("length"), _error("401 no")], False, "401 no"),
    ("10-pi-model-error-complete", 0, [_error("401 no")], True, "401 no"),
    ("11-pi-length-with-error-message", 0, [_LENGTH_WITH_ERROR], False, "boom"),
    (
        "12-velites-remediation-length",
        1,
        [_assistant("length"), _assistant("length"), _VALIDATION, _END],
        False,
        _TRUNCATED,
    ),
    # codex P2（4203615556）：补救轮正常完成仍缺产物 → 不是触顶。
    (
        "13-velites-remediation-stop",
        1,
        [_assistant("length"), _assistant("stop"), _VALIDATION, _END],
        False,
        _EXITED_1,
    ),
    (
        "14-velites-remediation-tooluse",
        1,
        [_assistant("length"), _assistant("toolUse"), _VALIDATION, _END],
        False,
        _EXITED_1,
    ),
    (
        "15-velites-remediation-retry-into-length",
        1,
        [_assistant("length"), *_RETRY, _assistant("length"), _VALIDATION, _END],
        False,
        _TRUNCATED,
    ),
    (
        "16-velites-missing-file-violation-only",
        1,
        [
            _assistant("length"),
            _assistant("length"),
            _contract_validation("output.json: missing required file"),
            _END,
        ],
        False,
        _TRUNCATED,
    ),
    (
        "17-velites-contract-parse-error",
        1,
        [_assistant("length"), _contract_validation("contract parse error: bad yaml"), _END],
        False,
        _EXITED_1,
    ),
    (
        "18-velites-content-violation",
        1,
        [
            _assistant("length"),
            _assistant("length"),
            _contract_validation(
                "output.json: missing required file", "report.md: missing heading ## Summary"
            ),
            _END,
        ],
        False,
        _EXITED_1,
    ),
    (
        "19-velites-budget-exceeded",
        1,
        [
            _assistant("toolUse"),
            _assistant("length"),
            _VALIDATION,
            {"type": "agent_end", "reason": "budget_exceeded"},
        ],
        False,
        _EXITED_1,
    ),
    (
        "20-velites-violation-outputs-present",
        1,
        [
            _assistant("stop"),
            _assistant("length"),
            _contract_validation("report.md: missing heading ## Summary"),
            _END,
        ],
        True,
        _EXITED_1,
    ),
    (
        "21-velites-unrecovered-model-error",
        1,
        [_assistant("length"), _error("429 rate limited"), _END],
        False,
        _EXITED_1,
    ),
    ("22-pi-exit-1", 1, [_assistant("length")], False, _EXITED_1),
    ("23-exit-2", 2, [_assistant("length")], False, "Agent process exited 2: velites: boom"),
    ("24-timeout", 124, [_assistant("length")], False, "Agent process timed out"),
    ("25-cancelled", 130, [_assistant("length")], False, "Agent Worker is shutting down"),
]


@pytest.mark.parametrize(
    ("exit_code", "events", "outputs_complete", "expected"),
    [pytest.param(*row[1:], id=row[0]) for row in _DECISION_TABLE],
)
def test_truncation_decision_table(
    tmp_path: Path, exit_code: int, events: list[dict], outputs_complete: bool, expected: str
) -> None:
    """按决策表逐行核对 Worker 上报的 status / error_message（含 model_error 列）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    if not outputs_complete:
        _drop_output(work_root)
    _write_events(work_root, events, ("velites: boom",))
    report = _report(work_root, exit_code=exit_code)
    assert report["exit_code"] == exit_code
    if expected == _TRUNCATED:
        assert report["status"] == "failed"
        message = report["error_message"]
        assert message.startswith(OUTPUT_TRUNCATED_PREFIX)
        count = sum(event == _assistant("length") for event in events)
        assert f"{count}x" in message and "output.json" in message
        assert "execution.thinking" in message
        # 归因只改失败原因，非零退出的 stderr 尾部仍作为证据面随 metadata 上报。
        assert exit_code == 0 or report["agent_stderr_tail"] == "velites: boom"
    elif exit_code == 130:
        assert report["status"] == "cancelled"
        assert report["error_message"] == expected
    elif expected:
        assert report["status"] == "failed"
        assert report["error_message"] == expected
    else:
        assert report["status"] == "completed"
        assert report["error_message"] == ""


def test_length_after_retry_clears_stale_model_error() -> None:
    """review P2：无 errorMessage 的 length 是一次成功返回的模型调用，清除旧的瞬态错误；
    带 errorMessage 的 length 仍记为错误。"""
    state: str | None = None
    for event in [*_RETRY, _assistant("length")]:
        state = fold_model_error(event, state)
    assert state is None
    assert fold_model_error(_LENGTH_WITH_ERROR, None) == "boom"
