"""#842 端到端：Worker prepare 车道上 events.jsonl 的工具输出脱敏。

真实链路（prepare → scan_and_compress → result.tar.gz）：bash 工具输出回显
的已注册密钥不得随压缩后的 events.jsonl 归档交付 Host；model_error 归因串
（message_end 的 errorMessage）同经快照脱敏进 metadata error_message 面。
共享桩/工具见 tests/workers/upload_queue_testlib.py。
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from tests.workers.upload_queue_testlib import QueueFakeClient, _queue, _task

pytestmark = pytest.mark.no_db


def _events_with_tool_output(work_root: Path, lines: list[dict]) -> Path:
    """exec-1 的 events.jsonl 直接写给定 JSON 事件行（含密钥回显形态）。"""
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    run_dir.mkdir(parents=True)
    events = run_dir / "events.jsonl"
    events.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    (work_root / "exec-1" / "job" / "output.json").write_text("{}", encoding="utf-8")
    return events


def _capture_archived_events(archive: Path) -> bytes:
    with tarfile.open(archive, "r:gz") as tar:
        member = next(m for m in tar.getmembers() if m.name.endswith("events.jsonl"))
        extracted = tar.extractfile(member)
        assert extracted is not None
        return extracted.read()


def test_tool_output_secret_redacted_before_archive_leaves_worker(
    tmp_path: Path, monkeypatch
) -> None:
    """#842 复现回归（修复前密钥逐字进归档）：agent 工具输出（bash env 回显）
    含已注册 env 密钥——归档里的压缩 events.jsonl 只剩 ***，行仍是合法 JSON、
    事件序列保真；执行归因不受影响（exit 0 照常 completed）。"""
    secret = "sk-live-supersecretgatewaytoken123"
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", secret)
    work_root = tmp_path / "work"
    _events_with_tool_output(
        work_root,
        [
            {"type": "session", "sessionId": "s-1"},
            {
                "type": "tool_execution_end",
                "toolCallId": "call-1",
                "toolName": "bash",
                "result": {"content": [{"type": "text", "text": f"LLM_GATEWAY_TOKEN={secret}"}]},
                "isError": False,
                "output_bytes": 123,
            },
            {"type": "agent_end"},
        ],
    )
    client = QueueFakeClient()
    archived: dict[str, bytes] = {}
    original_report = client.report

    def report_and_capture(execution_id, lease_id, metadata, archive):
        archived["events.jsonl"] = _capture_archived_events(archive)
        return original_report(execution_id, lease_id, metadata, archive)

    client.report = report_and_capture  # type: ignore[method-assign]
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()
    report = client.reports[0]
    assert report["status"] == "completed"

    face = archived["events.jsonl"]
    assert secret.encode() not in face
    assert b"***" in face
    kept = [json.loads(line) for line in face.decode("utf-8").splitlines()]
    assert [event["type"] for event in kept] == ["session", "tool_execution_end", "agent_end"]
    assert kept[1]["result"]["content"][0]["text"] == "LLM_GATEWAY_TOKEN=***"


def test_model_error_secret_redacted_into_metadata(tmp_path: Path, monkeypatch) -> None:
    """#842 metadata 面：provider 报错（message_end errorMessage）回显已注册
    密钥——error_message 归因串与压缩事件都只剩 ***（error_message 流进 DB 行
    与外部 error_summary 面）。"""
    secret = "sk-live-supersecretgatewaytoken123"
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", secret)
    work_root = tmp_path / "work"
    _events_with_tool_output(
        work_root,
        [
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "error",
                    "errorMessage": f"401 invalid key {secret}",
                },
            },
        ],
    )
    client = QueueFakeClient()
    archived: dict[str, bytes] = {}
    original_report = client.report

    def report_and_capture(execution_id, lease_id, metadata, archive):
        archived["events.jsonl"] = _capture_archived_events(archive)
        return original_report(execution_id, lease_id, metadata, archive)

    client.report = report_and_capture  # type: ignore[method-assign]
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()
    report = client.reports[0]
    assert report["status"] == "failed"
    assert report["error_message"] == "401 invalid key ***"
    assert secret.encode() not in archived["events.jsonl"]
