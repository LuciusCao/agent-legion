"""#842：events.jsonl 压缩保留事件的字符串值脱敏回归族。

Worker 上传前（压缩扫描同一趟）对保留事件的字符串值做脱敏：bash 工具输出
（``tool_execution_end`` 的 ``result.content[].text``）回显的已注册密钥不得
随压缩文件交付 Host、渲染进任务日志。只改字符串值（键名/数值/布尔/null
原样），输出仍是合法 JSON；``redactor=None``（Host 侧）逐字保留、零 JSON
改写开销；区间函数逃逸 fail-closed——该事件行整行丢弃，绝不以 raw 形态写出。
"""

import json

import pytest

from shared.pi_events import scan_and_compress_pi_events
from shared.redaction import REDACTED, SecretRedactor
from tests.helpers.secret_spans import literal_spans

pytestmark = pytest.mark.no_db


def _tool_end(text: str) -> dict:
    return {
        "type": "tool_execution_end",
        "toolCallId": "call-1",
        "toolName": "bash",
        "result": {"content": [{"type": "text", "text": text}]},
        "isError": False,
        "output_bytes": 12345,
    }


def _compress(tmp_path, events: list[dict], *secrets: str) -> list[dict]:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    scan_and_compress_pi_events(
        events_path,
        redactor=SecretRedactor(
            literal_spans(*secrets), max((len(secret) for secret in secrets), default=0)
        ),
    )
    return [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]


def test_tool_output_secret_redacted_in_compressed_events(tmp_path):
    """#842 复现形态（修复前密钥逐字随压缩文件外发）：bash ``env`` 回显已注册
    的 LLM_GATEWAY_TOKEN——压缩后只剩 ***，事件结构与其余字段原样。"""
    secret = "sk-live-supersecretgatewaytoken123"
    kept = _compress(
        tmp_path,
        [
            {"type": "session", "sessionId": "s-1"},
            _tool_end(f"LLM_GATEWAY_TOKEN={secret}\nPATH=/usr/bin"),
        ],
        secret,
    )
    assert [event["type"] for event in kept] == ["session", "tool_execution_end"]
    text = kept[1]["result"]["content"][0]["text"]
    assert secret not in text
    assert text == f"LLM_GATEWAY_TOKEN={REDACTED}\nPATH=/usr/bin"
    tool = kept[1]
    assert tool["toolCallId"] == "call-1"
    assert tool["toolName"] == "bash"
    assert tool["isError"] is False
    assert tool["output_bytes"] == 12345
    assert kept[0]["sessionId"] == "s-1"


def test_single_line_pem_and_json_escaped_newline_forms(tmp_path):
    """三形态回归：单行密钥 / 多行 PEM 字面量（真实换行）/ PEM 在 JSON 行里的
    ``\\n`` 转义形态（json.loads 解码成真实换行后整值命中，输出重转义合法）。"""
    single = "zz-json-token-" + "x" * 60
    pem = "\n".join(
        ["-----BEGIN PRIVATE KEY-----", "ABCDEFGHIJKLMNOP" * 4, "-----END PRIVATE KEY-----"]
    )
    kept = _compress(
        tmp_path,
        [
            _tool_end(f"token={single}"),
            _tool_end(pem),
            _tool_end(f"dump:\n{pem}\nafter"),
            {"type": "agent_end"},
        ],
        single,
        pem,
    )
    assert kept[0]["result"]["content"][0]["text"] == f"token={REDACTED}"
    assert kept[1]["result"]["content"][0]["text"] == REDACTED
    assert kept[2]["result"]["content"][0]["text"] == f"dump:\n{REDACTED}\nafter"
    assert kept[3] == {"type": "agent_end"}


def test_message_end_content_and_toolcall_arguments_redacted(tmp_path):
    """message_end 的 content（text / thinking / toolCall.arguments 对象）字符串
    值同样脱敏——助手消息回显密钥的路径不止工具输出。"""
    secret = "zz-msg-token-" + "m" * 50
    events = [
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": f"auth with {secret}"},
                    {"type": "text", "text": f"failed: {secret}"},
                    {
                        "type": "toolCall",
                        "name": "bash",
                        "id": "call-2",
                        "arguments": {"command": f"echo {secret}", "env": {"K": secret}},
                    },
                ],
                "stopReason": "stop",
            },
        }
    ]
    kept = _compress(tmp_path, events, secret)
    content = kept[0]["message"]["content"]
    assert content[0]["thinking"] == f"auth with {REDACTED}"
    assert content[1]["text"] == f"failed: {REDACTED}"
    assert content[2]["arguments"] == {"command": f"echo {REDACTED}", "env": {"K": REDACTED}}
    assert content[2]["name"] == "bash"
    assert kept[0]["message"]["stopReason"] == "stop"


def test_non_string_leaves_and_keys_untouched(tmp_path):
    """只改字符串值：数值/布尔/null/嵌套列表原样，键名（含密钥名与密钥值形态
    的键）不动——结构保真，输出仍是合法 JSON。"""
    secret = "zz-leaf-token-" + "l" * 50
    events = [
        {
            "type": "tool_execution_end",
            "toolCallId": "call-1",
            "toolName": "bash",
            "result": {"content": [{"type": "text", "text": f"env: {secret}"}]},
            "isError": False,
            "output_bytes": 7,
            "timing": {"totalMs": 12.5, "firstByteMs": 3, "reapMs": None},
            "mixed": [1, 2.5, True, None, "keep", secret],
            "LLM_GATEWAY_TOKEN": "not-a-secret-value",
            secret: 1,
        }
    ]
    kept = _compress(tmp_path, events, secret)
    tool = kept[0]
    assert tool["result"]["content"][0]["text"] == f"env: {REDACTED}"
    assert tool["timing"] == {"totalMs": 12.5, "firstByteMs": 3, "reapMs": None}
    assert tool["mixed"] == [1, 2.5, True, None, "keep", REDACTED]
    assert tool["LLM_GATEWAY_TOKEN"] == "not-a-secret-value"
    assert tool[secret] == 1  # 密钥值形态的键：只脱值不脱键（文档化边界）


def test_clean_events_keep_original_bytes_with_and_without_redactor(tmp_path):
    """无命中（redactor=None 的 Host 路径，或快照区间函数空转的 Worker 路径）
    时相关事件行逐字节保留（含冒号后空白的 pretty 形态）——零 JSON 改写开销，
    且两条路径输出字节一致（嵌入预检等大小口径不因脱敏开关漂移）。"""
    lines = [
        '{"type": "session", "sessionId": "s-1"}',
        '{"type":"message_update","assistantMessageEvent":{"type":"text_delta"}}',
        '{"type": "agent_end"}',
    ]
    expected = '{"type": "session", "sessionId": "s-1"}\n{"type": "agent_end"}\n'
    for redactor in (None, SecretRedactor(lambda _text: [], 0)):
        events_path = tmp_path / f"events-{redactor is not None}.jsonl"
        events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        scan_and_compress_pi_events(events_path, redactor=redactor)
        assert events_path.read_text(encoding="utf-8") == expected


def test_redactor_error_drops_event_line_fail_closed(tmp_path):
    """区间函数在事件字符串上逃逸 = fail-closed：该事件行整行丢弃（绝不以 raw
    形态写进压缩文件），其余事件照常保留，压缩照常完成。"""
    secret = "zz-boom-token-" + "b" * 50

    def exploding_on_secret(text: str) -> list[tuple[int, int]]:
        if secret in text:
            raise ValueError("redactor exploded")
        return literal_spans(secret)(text)

    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        json.dumps({"type": "session"})
        + "\n"
        + json.dumps(_tool_end(f"env: {secret}"))
        + "\n"
        + json.dumps({"type": "agent_end"})
        + "\n",
        encoding="utf-8",
    )
    _, original, compressed, _ = scan_and_compress_pi_events(
        events_path, redactor=SecretRedactor(exploding_on_secret, len(secret))
    )
    assert original > 0 and compressed > 0
    kept = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert [event["type"] for event in kept] == ["session", "agent_end"]
    assert secret not in events_path.read_text(encoding="utf-8")


def test_model_error_attribution_string_redacted(tmp_path):
    """#842：model_error 归因串（provider 报错回显密钥）流进 result metadata /
    error_message 外部面——扫描返回值同经快照脱敏。"""
    secret = "sk-live-supersecretgatewaytoken123"
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "error",
                    "errorMessage": f"401 invalid key {secret}",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    model_error, _, _, _ = scan_and_compress_pi_events(
        events_path, redactor=SecretRedactor(literal_spans(secret), len(secret))
    )
    assert model_error == f"401 invalid key {REDACTED}"
    assert secret not in events_path.read_text(encoding="utf-8")
