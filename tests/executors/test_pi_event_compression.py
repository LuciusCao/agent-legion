import json

from server.app.services.job_log_renderer import _parse_pi_events
from shared.pi_events import compress_pi_events
from tests.helpers.cjk_straddle import cjk_line_with_straddling_secret


def test_compress_pi_events_keeps_renderable_events(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                '{"type":"session"}',
                '{"type":"agent_start"}',
                '{"type":"turn_start"}',
                '{"type":"message_start"}',
                '{"type":"message_update","assistantMessageEvent":{"type":"thinking_delta","delta":"hello"}}',
                '{"type":"message_update","assistantMessageEvent":{"type":"thinking_delta","delta":" world"}}',
                '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"final"}]}}',
                '{"type":"tool_execution_start"}',
                '{"type":"tool_execution_update"}',
                '{"type":"tool_execution_end"}',
                '{"type":"agent_end"}',
            ]
        )
        + "\n"
    )

    original, compressed = compress_pi_events(events)
    assert original > compressed

    lines = events.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 8
    assert all(json.loads(line)["type"] != "message_update" for line in lines)

    entries = _parse_pi_events(events)
    assert any("回复" in entry["title"] for entry in entries)


def test_compress_pi_events_skips_missing_file(tmp_path):
    missing = tmp_path / "events.jsonl"
    assert compress_pi_events(missing) == (0, 0)


def test_compress_pi_events_handles_invalid_json(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"agent_start"}\nnot json\n{"type":"message_end"}\n')
    original, compressed = compress_pi_events(events)
    assert compressed > 0
    assert '"type":"agent_start"' in events.read_text()
    assert "not json" not in events.read_text()


def test_scan_and_compress_matches_separate_calls(tmp_path):
    from server.app.workflows.pi_protocol import detect_model_error
    from shared.pi_events import scan_and_compress_pi_events

    payload = "\n".join(
        [
            '{"type":"session"}',
            '{"type":"message_start","message":{"role":"assistant"}}',
            '{"type":"message_update","assistantMessageEvent":{"type":"text_delta"}}',
            '{"type":"message_end","message":{"role":"assistant","stopReason":"toolUse"}}',
            '{"type":"message_end","message":{"role":"assistant","stopReason":"error","errorMessage":"terminated"}}',
            '{"type":"message_end","message":{"role":"assistant","stopReason":"stop"}}',
            '{"type":"tool_execution_end"}',
        ]
    )

    separate = tmp_path / "separate.jsonl"
    separate.write_text(payload + "\n")
    expected_error = detect_model_error(separate)
    compress_pi_events(separate)

    combined = tmp_path / "combined.jsonl"
    combined.write_text(payload + "\n")
    model_error, original, compressed, _ = scan_and_compress_pi_events(combined)

    assert model_error == expected_error is None
    assert original == len(payload) + 1
    assert compressed == combined.stat().st_size
    assert combined.read_text() == separate.read_text()


def test_scan_and_compress_reports_unrecovered_error(tmp_path):
    from shared.pi_events import scan_and_compress_pi_events

    events = tmp_path / "events.jsonl"
    events.write_text(
        '{"type":"message_end","message":{"role":"assistant","stopReason":"error","errorMessage":"400 bad request"}}\n'
    )
    model_error, original, compressed, _ = scan_and_compress_pi_events(events)
    assert model_error == "400 bad request"
    assert original > 0 and compressed > 0


def test_scan_and_compress_skips_missing_file(tmp_path):
    from shared.pi_events import scan_and_compress_pi_events

    assert scan_and_compress_pi_events(tmp_path / "missing.jsonl") == (None, 0, 0, b"")


def test_scan_and_compress_velites_retry_stream_judged_recovered(tmp_path):
    # velites retry pattern (same as Node Pi): each failed transient attempt
    # emits an error message_end + auto_retry_start; the later successful
    # message_end clears the error, so the run is judged recovered — and
    # auto_retry_start must survive compression (pi_events allowlist).
    from shared.pi_events import scan_and_compress_pi_events

    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                '{"type":"session","sessionId":"s1"}',
                '{"type":"message_start","message":{"role":"assistant"}}',
                '{"type":"message_end","message":{"role":"assistant","stopReason":"error","errorMessage":"provider call failed (transient): terminated"}}',
                '{"type":"auto_retry_start","attempt":1,"maxAttempts":4,"delayMs":1000,"error":"terminated"}',
                '{"type":"message_end","message":{"role":"assistant","stopReason":"stop","usage":{"input":1,"output":1,"cacheRead":0}}}',
                '{"type":"agent_end"}',
            ]
        )
        + "\n"
    )
    model_error, original, compressed, _ = scan_and_compress_pi_events(events)
    assert model_error is None, "recovered retry must not be judged a model failure"
    assert original > 0 and compressed > 0

    kept_types = [
        json.loads(line)["type"] for line in events.read_text(encoding="utf-8").strip().splitlines()
    ]
    assert "auto_retry_start" in kept_types
    assert kept_types == [
        "session",
        "message_start",
        "message_end",
        "auto_retry_start",
        "message_end",
        "agent_end",
    ]


def test_scan_and_compress_captures_stderr_tail(tmp_path):
    """#748: 非 JSON 行（合并进 stdout 管道的 agent stderr）在压缩 rewrite
    丢弃它们之前被保尾捕获——崩溃栈必须可从返回值取回。"""
    from shared.pi_events import scan_and_compress_pi_events

    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                '{"type":"session"}',
                "INFO: starting up",
                '{"type":"message_end","message":{"role":"assistant"}}',
                "thread panicked at src/main.rs:42:",
                "assertion `left == right` failed",
            ]
        )
        + "\n"
    )
    model_error, _, _, stderr_tail = scan_and_compress_pi_events(events)
    assert model_error is None
    assert (
        stderr_tail
        == b"INFO: starting up\nthread panicked at src/main.rs:42:\nassertion `left == right` failed"
    )
    # 压缩后的文件本身照旧只留 JSON 事件（stderr 只活在返回值里）。
    assert "panicked" not in events.read_text(encoding="utf-8")


def test_scan_and_compress_stderr_tail_bounded_keep_tail(tmp_path):
    """#748 有界性（#637 教训）：超限的 stderr 只保尾部、且上限是硬字节
    上限——单行巨型乱码与海量小行两种形态都不允许无界缓冲。"""
    from shared.pi_events import STDERR_TAIL_BYTES, scan_and_compress_pi_events

    events = tmp_path / "events.jsonl"
    events.write_text("x" * (STDERR_TAIL_BYTES * 4) + "\ncrash: the real cause\n")
    _, _, _, stderr_tail = scan_and_compress_pi_events(events)
    assert len(stderr_tail) <= STDERR_TAIL_BYTES
    assert stderr_tail.endswith(b"crash: the real cause")

    many = tmp_path / "many.jsonl"
    many.write_text("".join(f"noise-{i:05d}\n" for i in range(1000)) + "final: panic header\n")
    _, _, _, many_tail = scan_and_compress_pi_events(many)
    assert len(many_tail) <= STDERR_TAIL_BYTES
    assert many_tail.endswith(b"final: panic header")
    # 保尾：最早的无意义行已被挤掉。
    assert b"noise-00000" not in many_tail


def test_scan_and_compress_no_stderr_yields_empty_tail(tmp_path):
    from shared.pi_events import scan_and_compress_pi_events

    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"session"}\n{"type":"agent_end"}\n')
    _, _, _, stderr_tail = scan_and_compress_pi_events(events)
    assert stderr_tail == b""


def test_scan_and_compress_persists_tail_to_sink_at_scan_time(tmp_path):
    """#748 review P1：非空 tail 在扫描时刻即落盘 sink 文件（rewrite 之前），
    重入方（直传回落 / 重启恢复）读文件而非二次扫描。"""
    from shared.pi_events import scan_and_compress_pi_events

    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"session"}\nthread panicked at src/main.rs:42:\nassertion failed\n')
    sink = tmp_path / "agent-stderr.log"
    model_error, _, _, stderr_tail = scan_and_compress_pi_events(events, stderr_sink=sink)
    assert model_error is None
    assert (
        sink.read_bytes() == stderr_tail == b"thread panicked at src/main.rs:42:\nassertion failed"
    )
    # 二次扫描（events 已压缩）tail 为空——幂等锚点在文件里。
    _, _, _, second_scan = scan_and_compress_pi_events(events, stderr_sink=sink)
    assert second_scan == b""
    assert sink.read_bytes() == b"thread panicked at src/main.rs:42:\nassertion failed"


def test_scan_and_compress_empty_tail_writes_no_sink(tmp_path):
    """无 stderr 时不动 sink 文件（不留空文件占位、不覆盖既有内容）。"""
    from shared.pi_events import scan_and_compress_pi_events

    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"session"}\n{"type":"agent_end"}\n')
    sink = tmp_path / "agent-stderr.log"
    scan_and_compress_pi_events(events, stderr_sink=sink)
    assert not sink.exists()


def test_scan_and_compress_sink_write_failure_never_fails(tmp_path, monkeypatch):
    """sink 不可写（OSError）不炸压缩：返回值仍带 tail，压缩照常完成。"""
    from shared import pi_events
    from shared.pi_events import scan_and_compress_pi_events

    def broken_persist(_sink, _tail):
        raise OSError("disk full")

    monkeypatch.setattr(pi_events, "_persist_stderr_tail", broken_persist)
    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"session"}\npanic: real cause\n')
    _, original, compressed, stderr_tail = scan_and_compress_pi_events(
        events, stderr_sink=tmp_path / "agent-stderr.log"
    )
    assert original > 0 and compressed > 0
    assert stderr_tail == b"panic: real cause"


def test_scan_and_compress_redacts_sink_before_durable_write(tmp_path):
    """#748 R3（codex review P1）：redact 回调在 durable write 前生效——落盘的
    anchor 文件是脱敏后字节，返回值保持 RAW（shared 只管落盘面，调用方的出口
    面各自脱敏）。回调 None 时行为不变（raw 落盘，兼容直接调 shared 的场景）。"""
    from shared.pi_events import scan_and_compress_pi_events

    secret = "sk-live-supersecretgatewaytoken123"
    events = tmp_path / "events.jsonl"
    events.write_text(f'{{"type":"session"}}\nauth failed for {secret}\n')
    sink = tmp_path / "agent-stderr.log"
    _, _, _, stderr_tail = scan_and_compress_pi_events(
        events,
        stderr_sink=sink,
        redact=lambda raw: raw.replace(secret.encode(), b"***"),
    )
    assert stderr_tail == f"auth failed for {secret}".encode()  # 返回值 raw
    assert sink.read_bytes() == b"auth failed for ***"  # 落盘脱敏
    # 回调 None：raw 落盘（旧行为）。
    events2 = tmp_path / "events2.jsonl"
    events2.write_text(f'{{"type":"session"}}\nauth failed for {secret}\n')
    sink2 = tmp_path / "agent-stderr2.log"
    scan_and_compress_pi_events(events2, stderr_sink=sink2)
    assert sink2.read_bytes() == f"auth failed for {secret}".encode()


def test_persist_stderr_tail_cleans_staging_on_replace_failure(tmp_path, monkeypatch):
    """#748 R3（codex review P1）：os.replace 失败时 delete=False 的 staging
    文件必须被清理——不清理则永久残留（内容虽已脱敏，但会随 run 目录进归档）。"""
    import os

    from shared import pi_events
    from shared.pi_events import _persist_stderr_tail

    def failing_replace(src, dst):
        raise OSError("cross-device link")

    monkeypatch.setattr(pi_events.os, "replace", failing_replace)
    try:
        _persist_stderr_tail(tmp_path / "agent-stderr.log", b"redacted tail")
    except OSError:
        pass  # 预期：replace 失败原样上抛（调用点 best-effort 捕获）
    finally:
        monkeypatch.setattr(pi_events.os, "replace", os.replace)
    assert list(tmp_path.glob(".agent-stderr.*")) == []  # staging 已清理
    assert not (tmp_path / "agent-stderr.log").exists()  # sink 未落盘


def test_scan_and_compress_redacts_secret_straddling_tail_cut(tmp_path):
    """#755 对抗复审 P3-2：密钥骑跨 8KB 保尾切割点时，「先切后脱敏」会让
    残段（整值匹配不上的尾部碎片）明文落进 sink。修复后双保险：脱敏窗口
    比切割界宽（_REDACT_WINDOW_MARGIN，跨点密钥整值命中），且最终切片
    按行对齐（切割行保守丢弃）——sink 与返回值都不留残段。"""
    from shared.pi_events import STDERR_TAIL_BYTES, scan_and_compress_pi_events

    secret = "sk-live-" + "s" * 92  # 100 字节
    # CJK 噪音（3 字节/字）把密钥行推上 8KB 字节切割界：密钥行 116 字节 +
    # "\n" + 2714 字噪音 = 8259 字节，切割点落在密钥第 51 字节处。
    noise = "噪" * 2714
    events = tmp_path / "events.jsonl"
    events.write_text(f'{{"type":"session"}}\nauth failed for {secret}\n{noise}\n')
    sink = tmp_path / "agent-stderr.log"
    _, _, _, tail = scan_and_compress_pi_events(
        events,
        stderr_sink=sink,
        redact=lambda raw: raw.replace(secret.encode(), b"***"),
    )
    # 返回值 RAW 但按行对齐：骑跨切割点的密钥行被保守丢弃，残段不外泄。
    assert len(tail) <= STDERR_TAIL_BYTES
    assert b"sk-live" not in tail
    assert b"ssss" not in tail
    # sink：扩窗脱敏整值命中——连密钥行前缀都不留（行虽被切，整值已先替换）。
    persisted = sink.read_bytes()
    assert len(persisted) <= STDERR_TAIL_BYTES
    assert b"sk-live" not in persisted
    assert b"ssss" not in persisted
    assert b"***" in persisted


def test_scan_and_compress_redacts_secret_straddling_single_line_cut(tmp_path):
    """#755 codex review P1：单行 100KB 非 JSON stderr 在入 deque 前先被
    预截到 8KB——「先截后脱敏」时骑跨切割点的密钥只剩尾段（整值匹配不上），
    明文残段进 sink / 返回值 / 归档。收口后单行预截走统一漏斗
    _redact_then_tail（扩窗 → 脱敏 → 再截）：跨点密钥整值命中替换，
    残段不外泄。"""
    from shared.pi_events import STDERR_TAIL_BYTES, scan_and_compress_pi_events

    secret = "sk-live-" + "s" * 92  # 100 字符
    # 密钥骑跨 8KB 单行预截切割点：切割点落在密钥第 50 字符处。
    line = "a" * (100_000 - STDERR_TAIL_BYTES - 50) + secret + "b" * (STDERR_TAIL_BYTES - 50)
    events = tmp_path / "events.jsonl"
    events.write_text(f'{{"type":"session"}}\n{line}\n')
    sink = tmp_path / "agent-stderr.log"
    _, _, _, tail = scan_and_compress_pi_events(
        events,
        stderr_sink=sink,
        redact=lambda raw: raw.replace(secret.encode(), b"***"),
    )
    # 返回值：单行预截已脱敏——完整密钥与残段（"s" 碎片）都不留。
    assert len(tail) <= STDERR_TAIL_BYTES
    assert b"sk-live" not in tail
    assert b"ssss" not in tail
    assert b"***" in tail
    # sink：同源漏斗，同样整值命中。
    persisted = sink.read_bytes()
    assert len(persisted) <= STDERR_TAIL_BYTES
    assert b"sk-live" not in persisted
    assert b"ssss" not in persisted
    assert b"***" in persisted


def test_scan_and_compress_redact_callback_error_never_fails_scan(tmp_path):
    """#755 对抗复审 P3-4：redact 回调的非 OSError 逃逸（脱敏器自身炸掉）
    不得把 run 改判 failed——sink 落盘是 best-effort 观测面：压缩照常完成、
    返回值照常携带 raw tail，只是本次不留锚点。"""
    from shared.pi_events import scan_and_compress_pi_events

    def exploding_redact(_raw: bytes) -> bytes:
        raise ValueError("redactor exploded")

    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"session"}\npanic: real cause\n')
    sink = tmp_path / "agent-stderr.log"
    _, original, compressed, tail = scan_and_compress_pi_events(
        events, stderr_sink=sink, redact=exploding_redact
    )
    assert original > 0 and compressed > 0  # 压缩未因回调逃逸中断
    assert tail == b"panic: real cause"
    assert not sink.exists()


def test_scan_and_compress_cjk_single_line_byte_gate_redacts_straddling_secret(tmp_path):
    """#755 终审 P2-1（字符/字节口径混淆）：4100 字 CJK 单行（12300 字节）
    在旧的字符闸门（len(text) > 8192）下不进脱敏漏斗，最终字节兜底裸切，
    骑跨切割点的密钥尾段明文经 return tail → error_message/metadata 外泄
    （sink 面本就安全：扩窗脱敏先于切割）。修复后闸门按字节判定、单行先过
    漏斗脱敏，且无换行臂先对整体脱敏再保尾——return tail 与 sink 均无
    密钥与残段。"""
    from shared.pi_events import STDERR_TAIL_BYTES, scan_and_compress_pi_events

    secret = "zz-custom-gateway-token-" + "t" * 80  # 104 字节自定义形态密钥
    line = cjk_line_with_straddling_secret(secret)
    events = tmp_path / "events.jsonl"
    events.write_text(f'{{"type":"session"}}\n{line}\n')
    sink = tmp_path / "agent-stderr.log"
    _, _, _, tail = scan_and_compress_pi_events(
        events,
        stderr_sink=sink,
        redact=lambda raw: raw.replace(secret.encode(), b"***"),
    )
    # 修复前的泄漏形态：return tail 含密钥尾段残片（"ttt..."）——整值匹配
    # 不上，下游 stderr_evidence 的重脱敏也接不住。
    for face in (tail, sink.read_bytes()):
        assert len(face) <= STDERR_TAIL_BYTES
        assert secret.encode() not in face
        assert b"zz-custom" not in face  # 密钥前缀残段
        assert b"tttt" not in face  # 密钥尾段残片（切割点之后的半边）
        assert b"***" in face  # 密钥在切割前已整值脱敏
