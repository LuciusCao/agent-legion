"""Provider 鉴权错误在 velites 产物契约退出中的归因回归（#1502）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.workers.upload_queue_testlib import QueueFakeClient, _execution_dir, _queue, _task


def _report_with_model_error(work_root: Path, message: str, exit_code: int = 1) -> dict:
    _execution_dir(work_root)
    (work_root / "exec-1" / "job" / "output.json").unlink()
    events = [
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "stopReason": "error",
                "errorMessage": message,
            },
        },
        {"type": "agent_end", "error": message},
    ]
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=exit_code, command=("velites",)))
    queue.shutdown()
    return client.reports[0]


@pytest.mark.parametrize(
    "message",
    [
        "provider call failed: HTTP 401: Unauthorized",
        ("provider call failed: HTTP 200 error body: code=401: Authorization apikey is disabled"),
        "provider call failed: HTTP 200 error body: invalid API key",
        "provider call failed: HTTP 200 error body: API key was revoked",
    ],
)
def test_provider_auth_error_survives_output_contract_exit(tmp_path: Path, message: str) -> None:
    report = _report_with_model_error(tmp_path / "work", message)

    assert report["status"] == "failed"
    assert report["exit_code"] == 1
    assert report["error_message"] == message


@pytest.mark.parametrize(
    "message",
    [
        "provider call failed: HTTP 500: upstream unavailable",
        "provider call failed: HTTP 200 error body: code=400: bad request",
        "provider call failed: API key quota exceeded",
        "provider call failed: HTTP 400: invalid request: API key quota exceeded",
    ],
)
def test_non_auth_model_error_keeps_output_contract_exit(tmp_path: Path, message: str) -> None:
    report = _report_with_model_error(tmp_path / "work", message)

    assert report["status"] == "failed"
    assert report["error_message"] == "Agent process exited 1"


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(2, "Agent process exited 2"), (124, "Agent process timed out")],
)
def test_provider_auth_error_does_not_replace_other_exit_attribution(
    tmp_path: Path, exit_code: int, expected: str
) -> None:
    message = "provider call failed: HTTP 200 error body: code=401: API key is disabled"
    report = _report_with_model_error(tmp_path / "work", message, exit_code)

    assert report["status"] == "failed"
    assert report["error_message"] == expected
