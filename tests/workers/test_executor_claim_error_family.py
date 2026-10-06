"""executor claim 主循环的异常族收窄（issue #960，同 registration/retry.py 先例）。

只有「Host 暂时不可用」族——requests 传输错误与 Host 不合契约应答
（HostResponseError）——走 ClaimBackoffSequence 退避重试；Worker 侧编程错误
必须原样上抛出 main()（进程非 0 退出、traceback 进面板日志，交 supervisor
崩溃重启策略处理），不得被吞成退避把排障方向误导到网络。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import requests

from tests.workers.helpers import FakeClient, _prepare_main, _run_main
from worker import events
from worker import executor as agent_worker
from worker.execution import run as execution_run
from worker.host.errors import HostResponseError, TransientHostError


@pytest.mark.parametrize(
    "error",
    [
        HostResponseError("Agent claim failed: HTTP 502: b'bad gateway'"),
        TransientHostError("Agent claim failed: HTTP 503"),
        requests.ReadTimeout("read timed out"),
    ],
    ids=["host-response", "transient-host", "read-timeout"],
)
def test_main_backs_off_on_host_unavailable_family(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception
) -> None:
    fake = FakeClient(tmp_path / "unused.tar.gz")
    claim_calls = 0
    backoffs: list[str] = []

    def claim(
        worker_id: str,
        max_concurrency: int | None = None,
        max_code_concurrency: int | None = None,
    ) -> dict | None:
        nonlocal claim_calls
        claim_calls += 1
        if claim_calls == 1:
            raise error
        return None

    fake.claim = claim  # type: ignore[attr-defined]
    monkeypatch.setattr(
        events,
        "note_claim_backoff",
        lambda worker_id, exc, wait, failures: backoffs.append(type(exc).__name__),
    )
    thread, handlers, result = _run_main(monkeypatch, tmp_path, fake, {"claim_enabled": True})
    deadline = time.monotonic() + 10
    while claim_calls < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    handlers[agent_worker.signal.SIGTERM]()
    thread.join(timeout=10)

    assert claim_calls >= 2, "claim loop must survive a Host-unavailable error"
    assert result == [0]
    assert backoffs == [type(error).__name__]


@pytest.mark.parametrize("bug", [TypeError("bad arg"), KeyError("lease_id"), AttributeError("x")])
def test_main_programming_error_in_claim_pass_propagates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    bug: Exception,
) -> None:
    """#960 验收：claim pass 内的编程错误不再被伪装成 Host 不可用——不退避、
    不打 "Agent claim error … retrying"，main() 原样上抛（finally 停池）。"""
    fake = FakeClient(tmp_path / "unused.tar.gz")
    claim_calls = 0
    backoffs: list[object] = []

    def claim(
        worker_id: str,
        max_concurrency: int | None = None,
        max_code_concurrency: int | None = None,
    ) -> dict | None:
        nonlocal claim_calls
        claim_calls += 1
        if claim_calls > 1:
            # 回归护栏：若 bug 又被吞成退避，第二轮 claim 到达即停循环，
            # 让 main() 正常返回而让 pytest.raises 失败，而非挂死用例。
            handlers[agent_worker.signal.SIGTERM]()
            return None
        raise bug

    fake.claim = claim  # type: ignore[attr-defined]
    monkeypatch.setattr(execution_run, "run_execution", lambda *a, **k: None)
    monkeypatch.setattr(
        events, "note_claim_backoff", lambda worker_id, exc, wait, failures: backoffs.append(exc)
    )
    handlers = _prepare_main(monkeypatch, tmp_path, fake, {"claim_enabled": True})

    with pytest.raises(type(bug)):
        agent_worker.main()

    assert claim_calls == 1
    assert backoffs == []
    assert "Agent claim error" not in capsys.readouterr().out


def test_main_backs_off_on_lane_spawn_error_without_tracking_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#1051：执行车道起线程失败（Thread.start 资源类 RuntimeError）在 lane
    层包成 LaneSpawnError，claim 循环走专用退避臂存活——不当编程错误退出、
    不并入 Host 不可用族；该执行未入池（run_execution 从未被调用），日志
    点名交租约过期。"""
    from worker.execution import execution_lane

    fake = FakeClient(tmp_path / "unused.tar.gz")
    claim_calls = 0
    backoffs: list[str] = []
    ran: list[object] = []

    def claim(
        worker_id: str,
        max_concurrency: int | None = None,
        max_code_concurrency: int | None = None,
    ) -> dict | None:
        nonlocal claim_calls
        claim_calls += 1
        if claim_calls == 1:
            return {"execution_id": "exec-lane-1", "node_key": "n1", "kind": "agent"}
        return None

    def fail_start(self):  # noqa: ANN001
        raise RuntimeError("can't start new thread")

    fake.claim = claim  # type: ignore[attr-defined]
    monkeypatch.setattr(execution_lane._LaneWorker, "start", fail_start)
    monkeypatch.setattr(execution_run, "run_execution", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(
        events,
        "note_claim_backoff",
        lambda worker_id, exc, wait, failures: backoffs.append(type(exc).__name__),
    )
    thread, handlers, result = _run_main(monkeypatch, tmp_path, fake, {"claim_enabled": True})
    deadline = time.monotonic() + 10
    while claim_calls < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    handlers[agent_worker.signal.SIGTERM]()
    thread.join(timeout=10)

    assert claim_calls >= 2, "claim loop must survive a lane spawn failure"
    assert result == [0]
    assert backoffs == ["LaneSpawnError"]
    assert ran == []
    out = capsys.readouterr().out
    assert "Agent execution lane exhausted" in out
    assert "exec-lane-1" in out
    assert "Agent claim error" not in out
