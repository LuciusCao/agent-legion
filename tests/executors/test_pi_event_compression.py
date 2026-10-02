import json

import pytest

from server.app.services.job_log_renderer import _parse_pi_events
from shared.pi_events import STDERR_TAIL_BYTES, compress_pi_events
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
    anchor 文件是脱敏后字节。#755 对抗复审 P1-1 起返回值也走同一份脱敏后缓冲
    （调用方的重脱敏退化为纯防御网）。回调 None 时行为不变（raw 落盘，兼容
    直接调 shared 的场景）。"""
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
    assert stderr_tail == b"auth failed for ***"  # 返回值同走脱敏后缓冲
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


def test_scan_and_compress_long_secret_straddling_cut_with_widened_margin(tmp_path):
    """#755 codex P1：>512 字节的已注册密钥（PEM/长 JWT 形态）骑跨 8KB
    保尾界时，固定 512 的扩窗装不下整值，仍被「先切后脱敏」。修复后调用方
    按已注册最长密钥传 redact_secret_max_bytes，有效 margin 扩到 2000——
    sink 与 return 两面都不留密钥残段。"""
    from shared.pi_events import STDERR_TAIL_BYTES, scan_and_compress_pi_events

    secret = "pem-" + "k" * 1996  # 2000 字节，远超固定 512 窗口
    # 密钥中点骑跨 8KB 字节切割点：单行 16KB，切割点落在密钥中部。
    line = "a" * (STDERR_TAIL_BYTES - 1000) + secret + "b" * (STDERR_TAIL_BYTES - 1000)
    events = tmp_path / "events.jsonl"
    events.write_text(f'{{"type":"session"}}\n{line}\n')
    sink = tmp_path / "agent-stderr.log"
    _, _, _, tail = scan_and_compress_pi_events(
        events,
        stderr_sink=sink,
        redact=lambda raw: raw.replace(secret.encode(), b"***"),
        redact_secret_max_bytes=len(secret.encode()),
    )
    for face in (tail, sink.read_bytes()):
        assert len(face) <= STDERR_TAIL_BYTES
        assert secret.encode() not in face
        assert b"pem-" not in face  # 密钥前缀残段（切割点之前的半边）
        assert b"kkkk" not in face  # 密钥尾段残片
        assert b"***" in face  # 整值在切割前已脱敏


def test_scan_and_compress_multiline_pem_survives_no_fragment(tmp_path):
    """#755 对抗复审 P1-1：多行密钥（PEM）骑跨 deque 保留界——旧实现的
    保留预算只有裸 8192 字符，PEM 头部整行在任何扩窗脱敏运行之前就被
    popleft 丢弃（单行漏斗接不住换行密钥，sink 扩窗读不到已丢的行），
    body 行残段明文落锚点。修复后 deque 保留界按 redact_margin 放宽，
    且脱敏跑在 join 后的完整保留缓冲上（多行整值必然完整可见）、先于
    最终保尾切割——sink 与 return 两面不留任何 PEM body 行。"""
    from shared.pi_events import STDERR_TAIL_BYTES, scan_and_compress_pi_events

    body_line = "ABCDEFGHIJKLMNOP" * 4  # 64 字符 base64 形态行
    pem_lines = ["-----BEGIN PRIVATE KEY-----"] + [body_line] * 20 + ["-----END PRIVATE KEY-----"]
    secret = "\n".join(pem_lines)  # ~1330 字符的多行密钥
    # 几何：PEM 尾端距全文末尾 7800 字符（< 8192，尾段在最终切割内），
    # PEM 头部在切割点之前 ~930 字符（旧 8192 字符保留界会丢头 → 泄漏）。
    post = ["post-" + "y" * 60] * 120  # ~7800 字符
    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"session"}\n' + secret + "\n" + "\n".join(post) + "\n")
    sink = tmp_path / "agent-stderr.log"
    _, _, _, tail = scan_and_compress_pi_events(
        events,
        stderr_sink=sink,
        redact=lambda raw: raw.replace(secret.encode(), b"***"),
        redact_secret_max_bytes=len(secret.encode()),
    )
    for face in (tail, sink.read_bytes()):
        assert len(face) <= STDERR_TAIL_BYTES
        assert body_line.encode() not in face  # 任何 body 行残段
        assert b"PRIVATE KEY" not in face  # PEM 头尾标记残段
        assert b"***" in face  # 整值在切割前已脱敏


# -- #755 codex R8 P1：密钥形态矩阵（脱敏面归一化的结构性收口） --

_PEM_BODY_LINE = "ABCDEFGHIJKLMNOP" * 4  # 64 字符 base64 形态行


def _pem(trailing_newline: bool) -> str:
    """真实多行 PEM 形态（禁止单行假 PEM）；trailing_newline=True 即本轮
    真实案例——已注册的环境变量值以换行收尾。"""
    lines = ["-----BEGIN PRIVATE KEY-----"] + [_PEM_BODY_LINE] * 8 + ["-----END PRIVATE KEY-----"]
    pem = "\n".join(lines)
    return pem + "\n" if trailing_newline else pem


def _noise(n: int) -> str:
    """恰好 n 字节的 ASCII 噪音文本（99 字符一行，行间与收尾形态按 n 截齐）。"""
    unit = "p" * 99 + "\n"  # 100 字节
    return (unit * (n // 100 + 1))[:n]


def _straddle_text(secret: str, cut_offset: int) -> str:
    """stderr 文本：密钥居首，最终 8KB 字节切割点落在密钥第 cut_offset 字节
    （密钥头被切割丢弃、尾段残留——修复前残段明文落所有出口面）。"""
    filler = STDERR_TAIL_BYTES + cut_offset - len(secret.encode()) - 1
    return secret + "\n" + _noise(filler)


_SECRET_SINGLE = "zz-matrix-token-" + "x" * 60  # 单行自定义形态密钥
_SECRET_STRADDLE = "zz-straddle-" + "s" * 188  # 200 字节单行密钥
_SECRET_LEADING_WS = "  zz-lead-token-" + "w" * 40  # 前导空白是密钥的一部分
_SECRET_TRAILING_WS = "zz-trail-token-" + "y" * 30 + " \t"  # strip 可剥字符收尾
_SECRET_BLANK_LINE = (  # 含白行的多行密钥（证书链形态）
    "-----BEGIN CHAIN-----\nBODYLINE1\n\nBODYLINE2\n-----END CHAIN-----"
)
_PEM_FRAGMENTS = (b"PRIVATE KEY", _PEM_BODY_LINE.encode())

# (stderr 文本, 已注册密钥, 不得出现在任何出口面的碎片)——每条断言三面：
# stderr_sink 锚点文件 / 返回值 / 压缩后的结构化 events 文件。
_MATRIX = [
    pytest.param(
        "noise before\nauth failed for " + _SECRET_SINGLE + "\nnoise after\n",
        _SECRET_SINGLE,
        (b"zz-matrix", b"x" * 16),
        id="single-line-middle",
    ),
    pytest.param(
        "boot noise\n" + _pem(False),  # 文件不以换行收尾，末尾即密钥值
        _pem(False),
        _PEM_FRAGMENTS,
        id="pem-no-trailing-newline-at-eof",
    ),
    pytest.param(
        "boot noise\n" + _pem(True),  # R8 真实案例：stderr 恰好以带尾换行的密钥值收尾
        _pem(True),
        _PEM_FRAGMENTS,
        id="pem-trailing-newline-at-eof",
    ),
    pytest.param(
        _pem(True) + "post-noise-1\npost-noise-2\n",
        _pem(True),
        _PEM_FRAGMENTS,
        id="pem-trailing-newline-followed-by-noise",
    ),
    pytest.param(
        _SECRET_SINGLE + "\n" + _noise(500) + "\n",
        _SECRET_SINGLE,
        (b"zz-matrix", b"x" * 16),
        id="secret-followed-by-noise-lines",
    ),
    pytest.param(
        # 密钥（8191 字节）+ 行尾换行恰好填满 8KB 保留窗口。
        ("zz-fill-" + "f" * (STDERR_TAIL_BYTES - 9)) + "\n",
        "zz-fill-" + "f" * (STDERR_TAIL_BYTES - 9),
        (b"zz-fill", b"f" * 16),
        id="secret-exactly-fills-window",
    ),
    pytest.param(
        _straddle_text(_SECRET_STRADDLE, 8),
        _SECRET_STRADDLE,
        (b"zz-straddle", b"s" * 16),
        id="straddle-cut-at-secret-head",
    ),
    pytest.param(
        _straddle_text(_SECRET_STRADDLE, 100),
        _SECRET_STRADDLE,
        (b"zz-straddle", b"s" * 16),
        id="straddle-cut-at-secret-middle",
    ),
    pytest.param(
        _straddle_text(_SECRET_STRADDLE, 192),
        _SECRET_STRADDLE,
        (b"zz-straddle", b"s" * 16),
        id="straddle-cut-at-secret-tail",
    ),
    pytest.param(
        _straddle_text(_pem(True), 300),
        _pem(True),
        _PEM_FRAGMENTS,
        id="pem-trailing-newline-straddling-cut",
    ),
    pytest.param(
        _pem(False).replace("\n", "\r\n") + "\r\n",  # CRLF 行尾（通用换行翻译后命中）
        _pem(False),
        _PEM_FRAGMENTS,
        id="crlf-pem",
    ),
    pytest.param(
        _SECRET_LEADING_WS + "\nnoise\n",
        _SECRET_LEADING_WS,
        (b"zz-lead", b"w" * 16),
        id="leading-whitespace-secret",
    ),
    pytest.param(
        _SECRET_TRAILING_WS + "\nnoise\n",
        _SECRET_TRAILING_WS,
        (b"zz-trail", b"y" * 16),
        id="strippable-trailing-chars-secret",
    ),
    pytest.param(
        "pre\n" + _SECRET_BLANK_LINE + "\npost\n",
        _SECRET_BLANK_LINE,
        (b"BODYLINE", b"CHAIN"),
        id="blank-line-inside-secret",
    ),
]


@pytest.mark.parametrize(("stderr_text", "secret", "fragments"), _MATRIX)
def test_scan_and_compress_secret_form_matrix(tmp_path, stderr_text, secret, fragments):
    """#755 codex R8 P1 形态矩阵：deque 保留原始行分隔形态（strip + 重组的
    归一化只在脱敏之后的展示面发生）后，任何形态的已注册密钥——单行 / 多行
    PEM 无尾换行 / 多行 PEM 带尾换行（stderr 以其收尾，本轮真实案例）/ 恰好
    填满保留窗口 / 骑跨 8KB 最终切割点（头中尾偏移）/ 密钥后随噪声行 /
    CRLF 行尾 / 前导空白 / strip 可剥字符收尾 / 内含白行——在所有出口面
    （sink 锚点、返回值、压缩后的结构化 events）都被整值替换，连碎片都不留。"""
    from shared.pi_events import scan_and_compress_pi_events

    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"session"}\n' + stderr_text, encoding="utf-8")
    sink = tmp_path / "agent-stderr.log"
    _, _, _, tail = scan_and_compress_pi_events(
        events,
        stderr_sink=sink,
        redact=lambda raw: raw.replace(secret.encode(), b"***"),
        redact_secret_max_bytes=len(secret.encode()),
    )
    for face in (tail, sink.read_bytes()):
        assert len(face) <= STDERR_TAIL_BYTES
        assert secret.encode() not in face
        for fragment in fragments:
            assert fragment not in face
        assert b"***" in face  # 脱敏标记在（密钥在切割前已整值命中）
    # 结构化事件面：压缩后的 events.jsonl 只剩 JSON 行，同样无密钥字节。
    assert secret.encode() not in events.read_bytes()


# -- #755 codex R10 P1（顺序域）：淘汰切口也是切割——popleft 前必须先脱敏 --

_EVICT_HEAD_LINE = "zz-evict-head-" + "h" * 686  # 700 字符（两行密钥的首行）
_EVICT_TAIL_LINE = "zz-evict-tail-" + "t" * 638  # 652 字符（两行密钥的尾行）
_EVICT_TWO_LINE = _EVICT_HEAD_LINE + "\n" + _EVICT_TAIL_LINE  # 1353 字节
_EVICT_PEM = "\n".join(
    ["-----BEGIN PRIVATE KEY-----"] + [_PEM_BODY_LINE] * 20 + ["-----END PRIVATE KEY-----"]
)  # 1353 字符多行 PEM（22 行）

# (stderr 文本, 已注册密钥, margin 提示, 不得出现在任何出口面的碎片)。
# margin 提示刻意小于密钥的几何镜像生产真实形态：Worker 回调
# （redact_secrets_bytes）的可匹配集合含形态兜底 pass（_SECRET_SHAPES /
# Bearer），本就不计入 max_secret_bytes——淘汰安全性不得依赖「margin ≥
# 最长可匹配密钥」这一调用方契约（顺序域与值域的边界：值域扩窗照管
# 最终切割，淘汰切口必须由「先脱敏后切割」的顺序不变量兜住）。
_EVICT_MATRIX = [
    pytest.param(
        # codex 原文几何：两行密钥，前置噪音使次行恰好顶破预算（首轮淘汰
        # 在次行入缓冲时触发），跟进内容再把淘汰边界推过密钥首行——旧代码
        # popleft 首行后缓冲 ≤8192（无最终切割兜底），尾行整行明文落两面。
        _noise(7400) + _EVICT_TWO_LINE + "\n" + _noise(7400),
        _EVICT_TWO_LINE,
        0,
        (b"zz-evict-head", b"zz-evict-tail"),
        id="two-line-trigger-at-second-line",
    ),
    pytest.param(
        _EVICT_PEM + "\n" + _noise(7800),
        _EVICT_PEM,
        0,
        _PEM_FRAGMENTS,
        id="pem-boundary-near-head",
    ),
    pytest.param(
        _EVICT_PEM + "\n" + _noise(8100),
        _EVICT_PEM,
        0,
        _PEM_FRAGMENTS,
        id="pem-boundary-at-middle",
    ),
    pytest.param(
        _EVICT_PEM + "\n" + _noise(8160),
        _EVICT_PEM,
        0,
        _PEM_FRAGMENTS,
        id="pem-boundary-near-tail",
    ),
    pytest.param(
        # 密钥长度恰等于 margin：淘汰确实吃进密钥（跟进内容 >8192），旧代码
        # 靠「最终行对齐切割恰好吞掉残段」幸存——回归锁，防止几何微调反弹。
        _EVICT_PEM + "\n" + _noise(8300),
        _EVICT_PEM,
        len(_EVICT_PEM.encode()),
        _PEM_FRAGMENTS,
        id="pem-margin-exact",
    ),
    pytest.param(
        # 连续多轮淘汰后密钥跨淘汰边界（跟进内容远超一轮预算），margin 恰好
        # 覆盖——回归锁。
        _EVICT_PEM + "\n" + _noise(12000),
        _EVICT_PEM,
        len(_EVICT_PEM.encode()),
        _PEM_FRAGMENTS,
        id="pem-margin-exact-multi-round",
    ),
]


@pytest.mark.parametrize(("stderr_text", "secret", "margin_hint", "fragments"), _EVICT_MATRIX)
def test_scan_and_compress_eviction_never_splits_secret(
    tmp_path, stderr_text, secret, margin_hint, fragments
):
    """#755 codex R10 P1（顺序域）：流式 deque 的逐行淘汰（popleft）发生在
    整段脱敏（_redact_tail_buffer）之前，淘汰切口可以落在已注册多行密钥
    中间——头行被弃、尾段留存，整值匹配永远失配，残段明文落 sink 锚点 /
    返回值 / metadata（实测复现：可匹配密钥超出 margin 窗口覆盖、密钥后
    跟进 ~7.4K–8.2K 字符时）。修复后淘汰前先对完整缓冲整段脱敏——任何
    切割点（淘汰 popleft / 回调内部保尾切 / 最终保尾切）都只落在已脱敏
    文本上，切到 *** 无害。三面断言：sink / return / 结构化 events 都无
    密钥任何字节子串。"""
    from shared.pi_events import scan_and_compress_pi_events

    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"session"}\n' + stderr_text, encoding="utf-8")
    sink = tmp_path / "agent-stderr.log"
    _, _, _, tail = scan_and_compress_pi_events(
        events,
        stderr_sink=sink,
        redact=lambda raw: raw.replace(secret.encode(), b"***"),
        redact_secret_max_bytes=margin_hint,
    )
    for face in (tail, sink.read_bytes()):
        assert len(face) <= STDERR_TAIL_BYTES
        assert secret.encode() not in face
        for fragment in fragments:
            assert fragment not in face
    # 结构化事件面：压缩后的 events.jsonl 只剩 JSON 行，同样无密钥字节。
    assert secret.encode() not in events.read_bytes()
