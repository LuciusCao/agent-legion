"""#1147：prep 失败与 pump 写失败的 state 目录取证转储。

两条真实链路：
- upload 队列的 prepare 降级分支——运行目录被删（agent 自删）时失败上报前
  把 events 压缩副本 / stderr tail / 目录清单转储进 state 目录（work_root
  之外），脱敏后可检索；目录真缺失时转储「缺失说明」不崩溃；chmod 自锁的
  run 目录（EACCES 家族）各子步独立降级（评审 P3-1），扫描失败的 raw
  副本删除不留未脱敏文件（评审 P3-6）；
- reactor parse 池的 events 写失败——运行目录消失后流转向应急转储
  （积压与后续事件不再随目录灭失），未配置 evidence root 时保持既有
  注销降级；sink 的单行失败隔离与超长行单行化截断（评审 P3-2/P3-4）。

error_message 面：「运行目录缺失」（[work-dir-missing]，基建事故）与
「agent 无产出」（output_missing 族，执行问题）可 grep 区分；Host 侧分类
规则见 tests/services/test_failure_classification.py。共享桩/工具见
tests/workers/upload_queue_testlib.py。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from shared.redaction import SecretRedactor
from tests.helpers import wait_for_predicate
from tests.helpers.secret_spans import literal_spans
from tests.workers.upload_queue_testlib import QueueFakeClient, _execution_dir, _queue, _task
from worker import state_evidence, state_evidence_lines
from worker.execution.reactor import EventPumpReactor
from worker.state_evidence import EmergencyEventsSink

pytestmark = pytest.mark.no_db

SECRET = "sk-live-supersecretgatewaytoken123"


@pytest.fixture
def evidence_root(tmp_path: Path) -> Path:
    root = state_evidence.configure_evidence_root(tmp_path / "state")
    try:
        yield root
    finally:
        state_evidence.reset_evidence_root()


def _boom(task: Any) -> None:
    """替身 prepare_result：模拟扫描成功后的归档构建失败（#1147 时间线里
    运行目录在 tar 构建时已被删，但取证面要求 events 仍可读的场景）。"""
    raise RuntimeError("simulated post-scan failure")


# -- prep 降级分支的证据兜底 ------------------------------------------------------


def test_prep_failure_dumps_redacted_events_and_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evidence_root: Path
) -> None:
    """prepare 抛错降级时，events 压缩副本（走 delivery 同一扫描/脱敏）、
    stderr tail 与目录清单落进 state 目录；密钥回显在所有转储面上都只剩
    ***；error_message 带证据指针（非目录缺失族，不带 marker）。"""
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", SECRET)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    (run_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "type": "tool_execution_end",
                "toolCallId": "call-1",
                "result": {"content": [{"type": "text", "text": f"TOKEN={SECRET}"}]},
            }
        )
        + "\n"
        + f"crash echo TOKEN={SECRET}\n"
        + '{"type":"agent_end"}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr("worker.upload.prepare.prepare_result", _boom)
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    report = client.reports[0]
    assert report["status"] == "failed"
    assert report["error_message"] == (
        "result preparation failed: simulated post-scan failure; "
        f"evidence preserved at {evidence_root / 'exec-1__node_a'}"
    )
    incident = evidence_root / "exec-1__node_a"
    dumped_events = (incident / "events.jsonl").read_text(encoding="utf-8")
    assert SECRET not in dumped_events
    kept = [json.loads(line) for line in dumped_events.splitlines()]
    assert [event["type"] for event in kept] == ["tool_execution_end", "agent_end"]
    assert kept[0]["result"]["content"][0]["text"] == "TOKEN=***"
    stderr_tail = (incident / "agent-stderr.log").read_bytes()
    assert SECRET.encode() not in stderr_tail
    assert b"crash echo TOKEN=***" in stderr_tail
    record = json.loads((incident / "incident.json").read_text(encoding="utf-8"))
    assert record["events"] == "dumped"
    assert record["error"] == "simulated post-scan failure"
    assert record["pump_emergency_dump"] is False
    listing = (incident / "listing.txt").read_text(encoding="utf-8")
    assert "job/output.json" in listing
    assert "job/runs/node_a/worker/events.jsonl" in listing


def test_prep_failure_missing_run_dir_records_absence(tmp_path: Path, evidence_root: Path) -> None:
    """#1147 原始时间线：exit 0 但运行目录已被删 → 判败且 error_message 带
    [work-dir-missing] 标记与证据指针；events 已不可读时转储「缺失说明」
    不崩溃，最后已知目录清单照常落盘（run 目录从清单中消失）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    shutil.rmtree(work_root / "exec-1" / "job" / "runs" / "node_a" / "worker")
    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    report = client.reports[0]
    assert report["status"] == "failed"
    assert report["error_message"].startswith("[work-dir-missing] result preparation failed:")
    assert "No such file or directory" in report["error_message"]
    assert f"evidence preserved at {evidence_root / 'exec-1__node_a'}" in report["error_message"]
    incident = evidence_root / "exec-1__node_a"
    record = json.loads((incident / "incident.json").read_text(encoding="utf-8"))
    assert record["events"] == "absent"
    assert record["execution_dir_present"] is True
    assert not (incident / "events.jsonl").exists()
    assert not (incident / "agent-stderr.log").exists()
    listing = (incident / "listing.txt").read_text(encoding="utf-8")
    assert "job/output.json" in listing
    assert "job/runs/node_a/worker" not in listing  # 被删的 run 目录不再出现在清单里


def test_prep_failure_records_pump_emergency_dump_presence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evidence_root: Path
) -> None:
    """同一 incident 目录里已有 pump 应急转储时，prep 侧 incident.json 记
    pump_emergency_dump=True——排障时两个证据面互相可发现。"""
    monkeypatch.setattr("worker.upload.prepare.prepare_result", _boom)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    incident = evidence_root / "exec-1__node_a"
    incident.mkdir(parents=True)
    (incident / state_evidence.EMERGENCY_EVENTS_FILENAME).write_text("{}\n", encoding="utf-8")

    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    record = json.loads((incident / "incident.json").read_text(encoding="utf-8"))
    assert record["pump_emergency_dump"] is True


def test_prep_failure_eacces_run_dir_degrades_per_arm(tmp_path: Path, evidence_root: Path) -> None:
    """#1147 评审 P3-1：agent chmod 000 自身 run 目录 → is_file 在 Python 3.13
    对 EACCES 会抛，此前第一个 PermissionError 中止整个转储只留空 incident
    目录。修复后各子步独立降级：events 记 unreadable（含 errno），其余可用
    arm 照常产出（rglob 静默跳过不可读子树，job 侧清单仍在）。"""
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    os.chmod(run_dir, 0)
    client = QueueFakeClient()
    queue = _queue(client)
    try:
        queue.submit(_task(work_root, exit_code=0))
        queue.shutdown()
    finally:
        os.chmod(run_dir, 0o700)  # 让 tmp_path 收尾可清理

    report = client.reports[0]
    assert report["status"] == "failed"
    assert "result preparation failed" in report["error_message"]
    assert "[Errno 13]" in report["error_message"]
    assert not report["error_message"].startswith("[work-dir-missing]")  # 目录在，非缺失族
    incident = evidence_root / "exec-1__node_a"
    record = json.loads((incident / "incident.json").read_text(encoding="utf-8"))
    assert record["events"].startswith("unreadable:")
    assert "13" in record["events"]  # errno 随记
    assert record["execution_dir_present"] is True
    assert record["listing"].endswith("entries")
    assert not (incident / "events.jsonl").exists()
    assert not (incident / "agent-stderr.log").exists()
    listing = (incident / "listing.txt").read_text(encoding="utf-8")
    assert "job/output.json" in listing
    assert "worker/events.jsonl" not in listing  # 不可读子树的内容不出现在清单里


def test_prep_failure_scan_failure_discards_raw_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evidence_root: Path
) -> None:
    """#1147 评审 P3-6 安全 arm：转储副本的扫描失败（size>0 而 original==0）
    必须删除 raw 副本——未脱敏文件绝不留在 state 目录——并在 incident.json
    记 scan failed 说明。"""
    monkeypatch.setattr("worker.upload.prepare.prepare_result", _boom)
    monkeypatch.setattr(
        "worker.state_evidence.scan_and_compress_pi_events",
        lambda *args, **kwargs: (None, 0, 0, b""),
    )
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    secret = "sk-live-supersecretgatewaytoken123"
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", secret)
    (run_dir / "events.jsonl").write_text(
        json.dumps({"type": "tool_execution_end", "result": {"content": [{"text": secret}]}})
        + "\n",
        encoding="utf-8",
    )

    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    incident = evidence_root / "exec-1__node_a"
    record = json.loads((incident / "incident.json").read_text(encoding="utf-8"))
    assert record["events"].startswith("scan failed")
    assert "raw copy discarded" in record["events"]
    assert not (incident / "events.jsonl").exists()  # raw 副本已删
    remaining = [p.name for p in incident.iterdir()]
    assert set(remaining) == {"incident.json", "listing.txt"}


def test_events_copy_unlink_failure_does_not_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evidence_root: Path
) -> None:
    """评审 P3-2（收口轮，防御加固）：scan-failed 臂的副本删除自身再抛
    OSError（同进程相邻 syscall 双 EACCES 的理论形态）——修复前该异常逃出
    ``_dump_events_copy``、把 dump_prep_evidence 的 broad except 整臂炸掉
    （其余取证 arm 全不落盘）；修复后 unlink 各自 suppress：状态照常返回
    ``scan failed``（incident.json 照常落盘），raw 副本如实滞留（无 TTL
    证据目录的人工清理路径），error_message 不受影响。"""
    monkeypatch.setattr("worker.upload.prepare.prepare_result", _boom)
    monkeypatch.setattr(
        "worker.state_evidence.scan_and_compress_pi_events",
        lambda *args, **kwargs: (None, 0, 0, b""),  # scan-failed 臂
    )
    real_unlink = Path.unlink

    def failing_unlink(self: Path, missing_ok: bool = False):
        # 只对 state 目录里的 raw 副本失败（源 run 目录的清理不受影响）。
        if "evidence" in self.parts and self.name == "events.jsonl":
            raise OSError(13, "Permission denied")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    run_dir = work_root / "exec-1" / "job" / "runs" / "node_a" / "worker"
    secret = "sk-live-supersecretgatewaytoken123"
    (run_dir / "events.jsonl").write_text(
        json.dumps({"type": "tool_execution_end", "result": {"content": [{"text": secret}]}})
        + "\n",
        encoding="utf-8",
    )

    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    # 失败被 _dump_events_copy 内部吞掉：其余 arm 照常落盘（修复前 broad
    # except 炸掉整个转储，incident.json 缺失）。
    incident = evidence_root / "exec-1__node_a"
    record = json.loads((incident / "incident.json").read_text(encoding="utf-8"))
    assert record["events"].startswith("scan failed")  # 状态如实（非 unreadable）
    assert (incident / "listing.txt").is_file()  # 其余 arm 未被连坐
    raw_copy = incident / "events.jsonl"  # raw 副本滞留（防御语义，如实记录）
    assert raw_copy.is_file()
    report = client.reports[0]
    assert report["status"] == "failed"  # 结果仍可上报（不受取证臂影响）


def test_listing_redacts_secret_bearing_filenames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evidence_root: Path
) -> None:
    """#1168 F3 复现（修复前泄漏形态）：agent 把密钥写进文件名
    （``LLM_GATEWAY_TOKEN=sk-…`` 作为路径组件）→ 相对路径原样进无 TTL 的
    listing.txt，明文密钥长留 state 证据快照。修复后每条 entry 持久化前过
    span 脱敏，文件名里只剩 ``***``。"""
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", SECRET)
    monkeypatch.setattr("worker.upload.prepare.prepare_result", _boom)
    work_root = tmp_path / "work"
    _execution_dir(work_root)
    (work_root / "exec-1" / "job" / f"LLM_GATEWAY_TOKEN={SECRET}").write_text("x", encoding="utf-8")

    client = QueueFakeClient()
    queue = _queue(client)
    queue.submit(_task(work_root, exit_code=0))
    queue.shutdown()

    listing = (evidence_root / "exec-1__node_a" / "listing.txt").read_text(encoding="utf-8")
    assert SECRET not in listing
    assert f"job/LLM_GATEWAY_TOKEN={'*' * 3}" in listing
    assert "job/output.json" in listing  # 其余条目照常（可读形态保留）


def test_listing_entry_redaction_failure_degrades_entry(tmp_path: Path) -> None:
    """单条目脱敏逃逸（span 函数抛出族）fail-closed：该条目不得以未脱敏
    路径落盘——降级为固定占位（不携带任何原文），清单其余条目照常。"""
    secret = "sk-live-supersecretgatewaytoken123"

    def exploding_on_secret(text: str):
        if secret in text:
            raise ValueError("span function escaped")
        return []

    redactor = SecretRedactor(exploding_on_secret, 0)
    execution_dir = tmp_path / "exec"
    execution_dir.mkdir()
    (execution_dir / "plain.txt").write_text("x", encoding="utf-8")
    (execution_dir / f"token={secret}").write_text("x", encoding="utf-8")
    incident = tmp_path / "incident"
    incident.mkdir()  # 真实调用点由 _dump_prep_evidence 先建目录

    state_evidence._dump_listing(incident, execution_dir, redactor)

    listing = (incident / "listing.txt").read_text(encoding="utf-8")
    assert secret not in listing
    assert "plain.txt" in listing
    assert listing.count("<listing entry dropped: redaction failed>") == 1


# -- #1168 F3 矩阵：密钥在 entry 的每个位置都被整条脱敏 ------------------------


@pytest.mark.parametrize(
    "secret_relative",
    [
        "job/token={secret}.txt",  # 密钥在文件名（叶子）
        "job/token={secret}/out.json",  # 密钥在子目录名
        "job/a/token={secret}/b/c.txt",  # 密钥在深层路径段（中间段）
    ],
    ids=["filename", "subdir", "deep-segment"],
)
def test_listing_redacts_secret_in_every_path_position(
    tmp_path: Path, secret_relative: str
) -> None:
    """#1168 F3 矩阵：redact 作用于**完整相对路径串**（``as_posix()`` 整条），
    不是只对最后一段——密钥出现在文件名 / 子目录名 / 深层中间段都被整条
    命中替换为 ``***``；命中位置之外的路径结构（前后缀段）保留可读。"""
    secret = "sk-live-supersecretgatewaytoken123"
    redactor = SecretRedactor(literal_spans(secret), len(secret))
    execution_dir = tmp_path / "exec"
    target = execution_dir / secret_relative.format(secret=secret)
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")
    (execution_dir / "plain.txt").write_text("x", encoding="utf-8")  # 对照条目
    incident = tmp_path / "incident"
    incident.mkdir()

    state_evidence._dump_listing(incident, execution_dir, redactor)

    listing = (incident / "listing.txt").read_text(encoding="utf-8")
    assert secret not in listing
    assert "plain.txt" in listing  # 对照：普通文件条目不受影响
    lines = [line for line in listing.splitlines() if "job/" in line]
    # 命中段的条目（目录与文件）全替换为 ***；干净前缀段（如 job/a）原样。
    hit_lines = [line for line in lines if "token=" in line]
    assert hit_lines and all("***" in line and "token=sk" not in line for line in hit_lines)
    # 文件条目整条在场：命中段替换为 ***，其余路径结构（前后缀段）保留。
    assert secret_relative.format(secret="*" * 3) in lines


def test_tree_missing_inside_only_for_missing_tree_errnos(tmp_path: Path) -> None:
    """分类检测只认 ENOENT/ENOTDIR 且路径落在本 execution 目录内：权限错误
    （目录仍在）与外部路径不贴 [work-dir-missing] 标记，非 OSError 不参与。"""
    inside = str(tmp_path / "exec-1" / "job" / "runs" / "n" / "worker")
    assert state_evidence.tree_missing_inside(
        FileNotFoundError(2, "No such file or directory", inside), tmp_path / "exec-1"
    )
    assert state_evidence.tree_missing_inside(
        NotADirectoryError(20, "Not a directory", inside), tmp_path / "exec-1"
    )
    assert not state_evidence.tree_missing_inside(
        PermissionError(13, "Permission denied", inside), tmp_path / "exec-1"
    )
    assert not state_evidence.tree_missing_inside(
        FileNotFoundError(2, "No such file or directory", str(tmp_path / "elsewhere")),
        tmp_path / "exec-1",
    )
    assert not state_evidence.tree_missing_inside(RuntimeError("boom"), tmp_path / "exec-1")


# -- reactor parse 池写失败的应急转储 ---------------------------------------------


def _spawn_child(first: str, rest: list[str], pause: float) -> subprocess.Popen[bytes]:
    """先写 first（让主线程观察到写路径健康），停 pause 秒（主线程在此窗口
    删掉运行目录），再写 rest 的行，随后退出。"""
    script = "import sys, time\n"
    script += f"sys.stdout.write({first!r} + '\\n'); sys.stdout.flush()\n"
    script += f"time.sleep({pause})\n"
    for line in rest:
        script += f"sys.stdout.write({line!r} + '\\n'); sys.stdout.flush()\n"
    script += "time.sleep(0.05)\n"
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _fresh_reactor() -> EventPumpReactor:
    EventPumpReactor._singleton = None  # test isolation: never reuse across cases
    return EventPumpReactor.get()


def test_pump_write_failure_diverts_events_to_state_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evidence_root: Path
) -> None:
    """events.jsonl 写失败（运行目录被删）→ 流保持注册转向 state 目录取证：
    后续事件逐行脱敏落盘（JSON 事件结构保真 + 非文本行 span 脱敏），
    转储分流不算 parse_error、不注销流，join 语义照常。"""
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", SECRET)
    run_dir = tmp_path / "work" / "exec-9" / "job" / "runs" / "node_a" / "worker"
    run_dir.mkdir(parents=True)
    events = run_dir / "events.jsonl"
    events.touch()
    first = '{"type":"agent_start","seq":1}'
    diverted = [
        json.dumps(
            {
                "type": "tool_execution_end",
                "result": {"content": [{"type": "text", "text": f"TOKEN={SECRET}"}]},
            }
        ),
        f"crash echo TOKEN={SECRET}",
    ]
    proc = _spawn_child(first, diverted, pause=0.5)
    reactor = _fresh_reactor()
    try:
        handle = reactor.register(proc, str(events), "exec-9", "node_a")
        # 第一行成功落盘（写路径仍健康），随后删掉运行目录模拟 agent 自删。
        wait_for_predicate(lambda: events.read_text(encoding="utf-8") != "", timeout=10)
        shutil.rmtree(run_dir)
        proc.wait(timeout=15)
        handle.join(timeout=15)
    finally:
        proc.kill()
        reactor.shutdown()
        EventPumpReactor._singleton = None

    stream = handle._stream
    assert stream.evidence is not None  # 转储已分流
    assert stream.parse_error is None  # 未走注销降级
    dump = evidence_root / "exec-9__node_a" / "events-emergency.jsonl"
    content = dump.read_text(encoding="utf-8")
    assert SECRET not in content
    lines = content.splitlines()
    kept_event = json.loads(lines[0])
    assert kept_event["type"] == "tool_execution_end"
    assert kept_event["result"]["content"][0]["text"] == "TOKEN=***"
    assert lines[1] == "crash echo TOKEN=***"
    assert first not in content  # 删除前已交付原文件的行不重复进转储


def test_pump_write_failure_without_evidence_root_keeps_legacy_degradation(
    tmp_path: Path,
) -> None:
    """未配置 evidence root：写失败保持既有降级（parse_error + 流注销，
    事件面丢失），不产生任何 state 目录副作用。"""
    # 前提自持（防 xdist 邻测泄漏）：同进程跑过 executor main 的用例会把
    # 进程级 evidence 根留在模块全局上——本用例的「未配置」前提必须显式建立。
    state_evidence.reset_evidence_root()
    run_dir = tmp_path / "work" / "exec-x" / "job" / "runs" / "node_a" / "worker"
    run_dir.mkdir(parents=True)
    events = run_dir / "events.jsonl"
    events.touch()
    proc = _spawn_child(
        '{"type":"agent_start","seq":1}', ['{"type":"agent_end","seq":2}'], pause=0.2
    )
    reactor = _fresh_reactor()
    try:
        handle = reactor.register(proc, str(events), "exec-x", "node_a")
        wait_for_predicate(lambda: events.read_text(encoding="utf-8") != "", timeout=10)
        shutil.rmtree(run_dir)
        proc.wait(timeout=15)
        handle.join(timeout=15)
    finally:
        proc.kill()
        reactor.shutdown()
        EventPumpReactor._singleton = None
    assert handle._stream.parse_error is not None
    assert handle._stream.evidence is None
    assert not (tmp_path / "state").exists()


# -- 应急转储 sink 的单行语义（#1147 评审 P3-2 / P3-4） --------------------------


def test_emergency_sink_isolates_per_line_failures(tmp_path: Path) -> None:
    """单行渲染/脱敏/编码失败只丢该行（delivery 同款单行 fail-closed 语义），
    同批其余行照常落盘——不再让一个坏行炸掉整批（encode 曾在 per-line 守卫
    之外）。坏行本身绝不以 raw 形态落盘。"""
    secret = "sk-live-supersecretgatewaytoken123"

    def spans(text: str):
        if "poison" in text:
            raise RuntimeError("span function escaped")
        start = text.find(secret)
        return [(start, start + len(secret))] if start >= 0 else []

    redactor = SecretRedactor(spans, 0)
    sink = EmergencyEventsSink(tmp_path / "dump" / "events-emergency.jsonl", redactor)
    sink.write_lines(
        [
            b'{"type":"a","note":"poison line"}',
            json.dumps(
                {"type": "tool_execution_end", "result": {"content": [{"text": f"TOKEN={secret}"}]}}
            ).encode(),
            b"plain non-json line",
        ]
    )

    content = (tmp_path / "dump" / "events-emergency.jsonl").read_text(encoding="utf-8")
    lines = content.splitlines()
    assert len(lines) == 2  # 毒行整行丢弃，不落盘
    assert "poison" not in content
    kept_event = json.loads(lines[0])
    assert kept_event["result"]["content"][0]["text"] == "TOKEN=***"
    assert lines[1] == "plain non-json line"


def test_render_line_truncation_stays_one_physical_line() -> None:
    """超长行截断用内联占位（无换行）：一条事件仍是一个物理行，逐行
    json.loads 的取证工具不会把后续行误当续行。"""
    redactor = SecretRedactor(lambda text: [], 0)
    huge = json.dumps({"type": "x", "payload": "A" * 200_000})
    out = state_evidence_lines.render_line(huge.encode(), redactor)
    assert out.count("\n") == 1  # 仅行尾换行
    assert "chars truncated" in out
    physical = out.rstrip("\n")
    assert physical.startswith('{"type":')
    assert physical.endswith('"}')


def test_render_line_truncated_json_stays_parseable() -> None:
    """#1168 F4 复现（修复前形态）：>64KB 的 JSON 事件行中切后截断点落在
    字符串值中间，产物不再是合法 JSON（逐行 json.loads 报废）。修复后按
    「截字段值 + 重序列化」：转储行仍可 json.loads，事件结构与其余字段
    原样，超长字段带内联截断标记。"""
    redactor = SecretRedactor(lambda text: [], 0)
    huge = json.dumps(
        {
            "type": "tool_execution_end",
            "toolCallId": "call-1",
            "result": {"content": [{"type": "text", "text": "A" * 200_000}]},
        }
    )
    out = state_evidence_lines.render_line(huge.encode(), redactor)
    physical = out.rstrip("\n")
    assert out.count("\n") == 1
    event = json.loads(physical)  # 修复前：ValueError（截断破坏 JSON 语法）
    assert event["type"] == "tool_execution_end"
    assert event["toolCallId"] == "call-1"
    assert "chars truncated" in event["result"]["content"][0]["text"]
    assert len(physical) <= state_evidence_lines.MAX_DUMP_LINE_CHARS + 64


def test_render_line_truncation_at_escape_boundaries_stays_parseable() -> None:
    """#1168 F4 矩阵（转义边界格）：超长字符串密布 JSON 转义序列（``\\"`` /
    ``\\n`` / surrogate-pair ``\\ud83d\\ude00`` / ``\\\\``）——任何 32KB 处的
    中切都会劈开某个转义序列（悬空 ``\\`` / 半个 ``\\uXXXX`` 即非法 JSON）；
    重序列化路径的结构由 ``json.dumps`` 保证合法，截断标记落在值内。"""
    redactor = SecretRedactor(lambda text: [], 0)
    payload = ('quote " newline\n emoji 😀 backslash\\ tail ' + "B" * 64) * 900
    huge = json.dumps({"type": "x", "text": payload})
    assert "\\n" in huge and '\\"' in huge and "\\ud83d" in huge  # 转义序列确实密集在场
    assert len(huge) > state_evidence_lines.MAX_DUMP_LINE_CHARS

    out = state_evidence_lines.render_line(huge.encode(), redactor)

    event = json.loads(out.rstrip("\n"))  # 修复前：中切劈开转义 → JSONDecodeError
    assert event["type"] == "x"
    assert "chars truncated" in event["text"]


def test_render_line_deeply_nested_over_cap_stays_parseable() -> None:
    """#1168 F4 矩阵（深嵌套格）：深嵌套结构（300 层 dict + 每层长字符串）
    超过 64KB——``_capped_strings`` 的递归截值对任意深度保持结构合法，
    重序列化后仍可解析（深度本身不是截断失败面）。"""
    redactor = SecretRedactor(lambda text: [], 0)
    node: object = {"leaf": "C" * 500}
    for _ in range(300):
        node = {"child": node, "pad": "D" * 240}
    huge = json.dumps(node)
    assert len(huge) > state_evidence_lines.MAX_DUMP_LINE_CHARS

    out = state_evidence_lines.render_line(huge.encode(), redactor)

    parsed = json.loads(out.rstrip("\n"))  # 深嵌套 + 超限：结构合法可解析
    assert isinstance(parsed, dict)


def test_render_line_placeholder_when_structure_bloats() -> None:
    """结构超限（海量小字段：任何字符串 cap 下序列化都超 64KB）整体降级为
    单行占位事件 ``{"truncated": true, "original_bytes": N}``——仍是合法
    JSON、仍是单物理行，逐行解析工具不报废。"""
    redactor = SecretRedactor(lambda text: [], 0)
    bloated = json.dumps({"type": "x", **{f"k{i}": "v" * 64 for i in range(2000)}})
    assert len(bloated) > state_evidence_lines.MAX_DUMP_LINE_CHARS
    out = state_evidence_lines.render_line(bloated.encode(), redactor)
    physical = out.rstrip("\n")
    assert out.count("\n") == 1
    placeholder = json.loads(physical)
    assert placeholder == {"truncated": True, "original_bytes": len(bloated)}


def test_render_line_truncates_non_json_lines_inline() -> None:
    """非 JSON 行保持既有中切形态（首尾各半 + 内联标记）：单物理行、标记
    无换行——JSON 行的新语义不外溢到文本行。"""
    redactor = SecretRedactor(lambda text: [], 0)
    plain = "panic: " + "B" * 200_000
    out = state_evidence_lines.render_line(plain.encode(), redactor)
    assert out.count("\n") == 1
    assert "chars truncated" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out.rstrip("\n"))  # 文本行本来就不是 JSON
