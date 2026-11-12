"""#1175：应急事件转储的 JSON key 脱敏与 fail-closed 对抗回归。"""

import json
from pathlib import Path

import pytest

from shared.redaction import REDACTED, SecretRedactor
from tests.helpers.secret_spans import literal_spans
from worker.state_evidence import EmergencyEventsSink

pytestmark = pytest.mark.no_db


def _write_dump(path: Path, lines: list[bytes], redactor: SecretRedactor) -> str:
    EmergencyEventsSink(path, redactor).write_lines(lines)
    return path.read_text(encoding="utf-8")


def test_emergency_dump_redacts_nested_and_escaped_secret_keys(tmp_path: Path) -> None:
    """应急转储与压缩面共用 decoded-string 匹配；转义 key/value 的原文均不落盘。"""
    secret = 'zz-emergency-key-"\\-\n-汉-' + "k" * 40
    event = {
        "type": "tool_execution_end",
        "nested": {secret: f"value={secret}", "ordinary": ["keep", secret]},
    }
    wire = json.dumps(event).encode()
    assert json.dumps(secret).encode() in wire

    content = _write_dump(
        tmp_path / "events-emergency.jsonl",
        [wire],
        SecretRedactor(literal_spans(secret), len(secret)),
    )

    assert secret not in content
    assert json.dumps(secret) not in content
    kept = json.loads(content)
    assert list(kept["nested"]) == [REDACTED, "ordinary"]
    assert kept["nested"][REDACTED] == f"value={REDACTED}"
    assert kept["nested"]["ordinary"] == ["keep", REDACTED]


def test_emergency_dump_collision_drops_whole_line_and_keeps_batch(tmp_path: Path) -> None:
    """多个 secret key 碰撞时整行丢弃，同批前后事件保留，不能只覆盖成一个条目。"""
    first = "zz-emergency-first-" + "a" * 40
    second = "zz-emergency-second-" + "b" * 40
    redactor = SecretRedactor(literal_spans(first, second), max(len(first), len(second)))
    events = [
        {"type": "session", "sessionId": "before"},
        {
            "type": "tool_execution_end",
            "nested": {first: f"first-entry={first}", second: f"second-entry={second}"},
        },
        {"type": "agent_end", "reason": "after"},
    ]

    content = _write_dump(
        tmp_path / "events-emergency.jsonl",
        [json.dumps(event).encode() for event in events],
        redactor,
    )

    kept = [json.loads(line) for line in content.splitlines()]
    assert [event["type"] for event in kept] == ["session", "agent_end"]
    assert first not in content and second not in content
    assert "first-entry" not in content and "second-entry" not in content


def test_emergency_dump_key_span_error_drops_only_that_line(tmp_path: Path) -> None:
    """key 上的 span 错误不得写 raw 行或中断同批后续事件。"""
    secret = "zz-emergency-span-error-" + "e" * 40

    def exploding_on_secret(text: str):
        if secret in text:
            raise RuntimeError("span function escaped")
        return []

    content = _write_dump(
        tmp_path / "events-emergency.jsonl",
        [
            json.dumps({"type": "tool_execution_end", "nested": {secret: "clean"}}).encode(),
            b'{"type":"agent_end"}',
        ],
        SecretRedactor(exploding_on_secret, len(secret)),
    )

    assert [json.loads(line)["type"] for line in content.splitlines()] == ["agent_end"]
    assert secret not in content
