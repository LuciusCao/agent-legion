"""Worker 结果准备的单次输出触顶归因（#952）。

触顶（assistant ``stopReason=length``）只在声明产物缺失时改写失败原因，
不改变任何 run 的成败判定；崩溃/超时等其他退出码保持各自归因。共享桩见
tests/workers/upload_queue_testlib.py。
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
from tests.workers.upload_queue_testlib import QueueFakeClient, _execution_dir, _queue, _task

# velites 产物契约的证据事件：exit 1 只有带它才可归因为触顶。
_VALIDATION = {"type": "outputs_validation", "missing": ["output.json"]}


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
    # 触顶不进 model_error（exit 0 时 model_error 直接判失败）。
    assert model_error is None


def test_truncation_error_caps_listed_missing_outputs() -> None:
    message = output_truncation_error(3, [f"out/{i}.json" for i in range(12)])
    assert message.startswith(OUTPUT_TRUNCATED_PREFIX)
    assert "3x" in message
    assert "out/9.json" in message and "out/10.json" not in message
    assert "(+2 more)" in message


def test_exit_zero_truncated_with_missing_outputs_reports_truncation(tmp_path: Path) -> None:
    """pi 形态：exit 0、触顶、产物缺失 → 失败原因点名触顶而非笼统 Missing outputs。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _drop_output(work_root)
    _write_events(work_root, [_assistant("toolUse"), _assistant("length")])
    report = _report(work_root, exit_code=0)
    assert report["status"] == "failed"
    assert report["error_message"].startswith(OUTPUT_TRUNCATED_PREFIX)
    assert "output.json" in report["error_message"]
    assert "execution.thinking" in report["error_message"]


def test_truncated_run_with_all_outputs_still_completes(tmp_path: Path) -> None:
    """触顶但产物齐全：归因不改判，照旧 completed。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _write_events(work_root, [_assistant("length")])
    report = _report(work_root, exit_code=0)
    assert report["status"] == "completed"
    assert report["error_message"] == ""


def test_velites_contract_exit_one_attributes_truncation(tmp_path: Path) -> None:
    """velites 产物契约退出（exit 1）+ 触顶 + 缺产物：触顶归因取代 stderr 摘要，
    stderr 尾部仍随 metadata 作为证据面。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _drop_output(work_root)
    events = [_assistant("length"), _assistant("length"), _VALIDATION, {"type": "agent_end"}]
    _write_events(work_root, events, ("velites: missing",))
    report = _report(work_root, exit_code=1)
    assert report["status"] == "failed"
    assert report["exit_code"] == 1
    assert report["error_message"].startswith(OUTPUT_TRUNCATED_PREFIX)
    assert "2x" in report["error_message"]
    assert report["agent_stderr_tail"] == "velites: missing"


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(2, "Agent process exited 2"), (124, "Agent process timed out")],
)
def test_other_exit_codes_keep_their_attribution(
    tmp_path: Path, exit_code: int, expected: str
) -> None:
    """崩溃 / 超时退出不被触顶归因覆盖（它们有更直接的原因）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _drop_output(work_root)
    _write_events(work_root, [_assistant("length")])
    report = _report(work_root, exit_code=exit_code)
    assert report["status"] == "failed"
    assert report["error_message"] == expected


def test_missing_outputs_without_truncation_unchanged(tmp_path: Path) -> None:
    """未触顶的缺产物 run：worker 侧照旧 completed（由 Host 判 Missing outputs）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _drop_output(work_root)
    _write_events(work_root, [_assistant("stop")])
    report = _report(work_root, exit_code=0)
    assert report["status"] == "completed"


def test_unrecovered_model_error_keeps_precedence(tmp_path: Path) -> None:
    """exit 0 时未恢复的模型调用错误比触顶更直接，归因保持 model_error。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _drop_output(work_root)
    failed = {"role": "assistant", "stopReason": "error", "errorMessage": "401 unauthorized"}
    _write_events(work_root, [_assistant("length"), {"type": "message_end", "message": failed}])
    report = _report(work_root, exit_code=0)
    assert report["status"] == "failed"
    assert report["error_message"] == "401 unauthorized"


_MODEL_ERROR = {
    "type": "message_end",
    "message": {"role": "assistant", "stopReason": "error", "errorMessage": "429 rate limited"},
}


@pytest.mark.parametrize(
    "events",
    [
        # 早轮触顶 → 后续未恢复的模型错误（velites 出错直接 break，不发 outputs_validation）。
        pytest.param([_assistant("length"), _MODEL_ERROR, {"type": "agent_end"}], id="model-error"),
        # 早轮触顶 → max_turns 等预算耗尽（收尾轮后仍缺产物）。
        pytest.param(
            [
                _assistant("length"),
                _assistant("stop"),
                _VALIDATION,
                {"type": "agent_end", "reason": "budget_exceeded"},
            ],
            id="budget-exceeded",
        ),
        # pi 的 exit 1 是进程失败：没有 outputs_validation，不能归因为触顶。
        pytest.param([_assistant("length")], id="pi-exit-1"),
    ],
)
def test_exit_one_with_more_direct_cause_is_not_rewritten(
    tmp_path: Path, events: list[dict]
) -> None:
    """review B2：exit 1 下更直接的原因（模型错误 / 预算耗尽 / 非契约退出）不被改写。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    _drop_output(work_root)
    _write_events(work_root, events, ("velites: boom",))
    report = _report(work_root, exit_code=1)
    assert report["status"] == "failed"
    assert report["error_message"] == "Agent process exited 1: velites: boom"


def test_truncation_message_mentions_context_window() -> None:
    """Anthropic 的 model_context_window_exceeded 同样映射为 length，文案需覆盖。"""
    assert "context window" in output_truncation_error(1, ["a.json"])
