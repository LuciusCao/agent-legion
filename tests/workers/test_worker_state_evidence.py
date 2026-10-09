"""#1147：prep 失败与 pump 写失败的 state 目录取证转储。

两条真实链路：
- upload 队列的 prepare 降级分支——运行目录被删（agent 自删）时失败上报前
  把 events 压缩副本 / stderr tail / 目录清单转储进 state 目录（work_root
  之外），脱敏后可检索；目录真缺失时转储「缺失说明」不崩溃；
- reactor parse 池的 events 写失败——运行目录消失后流转向应急转储
  （积压与后续事件不再随目录灭失），未配置 evidence root 时保持既有
  注销降级。

error_message 面：「运行目录缺失」（[work-dir-missing]，基建事故）与
「agent 无产出」（output_missing 族，执行问题）可 grep 区分；Host 侧分类
规则见 tests/services/test_failure_classification.py。共享桩/工具见
tests/workers/upload_queue_testlib.py。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.helpers import wait_for_predicate
from tests.workers.upload_queue_testlib import QueueFakeClient, _execution_dir, _queue, _task
from worker import state_evidence
from worker.execution.reactor import EventPumpReactor

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
