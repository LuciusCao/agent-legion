"""Claim-loop budget regressions (issue #534, split from test_agent_worker.py).

The per-pool claim-budget guard in worker/executor.py's claim loop: an
agent pool clamped to 0 (ramp-up tier / capacity full / upload backpressure)
must not borrow the code pool's budget to keep claiming agent executions —
and the one cross-pool claim that Host still grants must be SUBMITTED for
execution (heartbeat + result channels alive), not dropped to lease expiry.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tests.workers.test_agent_worker import FakeClient, _claim, _run_main
from worker import events as agent_worker_events
from worker import executor as agent_worker


def test_main_agent_pool_exhausted_does_not_borrow_code_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#534：agent 池被钳到 0 时不得借 code 池预算继续领 agent 执行。

    场景：max_concurrency=1 + ramp_up initial=1（首单在跑后 agent 预算 0）、
    max_code_concurrency=32（code 池满额），Host 持续只发 agent 活。旧循环
    条件 ``budget[agent] + budget[code] > 0`` 借 code 预算放行，agent 预算
    被扣成负值（实测 -31，#471 爬坡门被完全绕过）。修后：越池的那一单
    **先提交执行**（codex P1 复审：break 若在 submit 前，Host 已记 claimed
    的执行不跑/不心跳/不报结果，悬挂到租约过期且爬坡期逐轮累积）再终止
    本轮——断言预算全程非负、每 pass 至多 1 单、且每个越池单都被 submit。"""
    fake = FakeClient(tmp_path / "unused.tar.gz")
    claim_calls = 0
    first_claimed = threading.Event()
    claim_lock = threading.Lock()

    def claim(
        worker_id: str,
        max_concurrency: int | None = None,
        max_code_concurrency: int | None = None,
    ) -> dict | None:
        nonlocal claim_calls
        with claim_lock:
            claim_calls += 1
            if claim_calls == 1:
                first_claimed.set()
                # 首单占住执行（run_execution 阻塞在 release 上）。
        # Host 按 #501 的目标容量记账，不替本地预算把门：钳住后仍发 agent 活。
        return _claim(f"exec-{claim_calls}")

    release = threading.Event()

    def block_execution(  # type: ignore[no-untyped-def]
        client,
        claimed,
        work_root,
        environment,
        interval,
        stop,
        grace,
        status,
        uploads,
        slots,
        heartbeat_registry=None,
    ):
        release.wait(timeout=10)

    fake.claim = claim  # type: ignore[method-assign]
    # 拦截 claim.attempt 的预算快照（issue #534 验收面：agent_budget 不再
    # 出现负值序列）——旧 bug 借 code 预算把 agent 预算扣到 -31。
    attempts: list[dict] = []
    monkeypatch.setattr(
        agent_worker_events,
        "note_claim_attempt",
        lambda worker_id, budget, depth, enabled: attempts.append(dict(budget)),
    )
    # codex P1（#535 复审）：越池单必须被提交执行——run_execution 是
    # pool.submit 的必经函数（executor 的 pool 是 main 局部变量包不到），
    # counting wrapper 记录每次提交的 claim 载荷。
    submitted: list[object] = []

    def counting_execution(*args, **kwargs):  # type: ignore[no-untyped-def]
        submitted.append(args[1] if len(args) > 1 else None)
        return block_execution(*args, **kwargs)

    monkeypatch.setattr(agent_worker, "run_execution", counting_execution)

    updates = {
        "claim_enabled": True,
        "max_concurrency": 1,
        "max_code_concurrency": 32,
        "ramp_up": {"initial": 1, "step": 1, "interval_seconds": 60},
    }
    thread, handlers, result = _run_main(monkeypatch, tmp_path, fake, updates)
    assert first_claimed.wait(timeout=5), "first claim never happened"
    # 观察足够多的 pass（interval=60s 档位不动，agent 池恒 0）。
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        time.sleep(0.05)
    handlers[agent_worker.signal.SIGTERM]()
    release.set()
    thread.join(timeout=10)
    assert result == [0]
    # 铁证一（#534 验收）：整个窗口内 agent 预算从未为负。
    assert attempts, "claim.attempt events must have been emitted"
    assert all(a["agent"] >= 0 for a in attempts), [a for a in attempts if a["agent"] < 0]
    # 铁证二：越池单本身被照单收下（claim > 1）且每个都提交了执行
    # （codex P1：submitted 计数与 claim 计数一致——无悬挂租约）。
    assert claim_calls >= 2, "the cross-pool claim itself must still be accepted"
    assert len(submitted) == claim_calls, (
        f"every claimed execution must be submitted: {len(submitted)} submitted "
        f"vs {claim_calls} claims"
    )
    # 铁证三：每个 pass 至多 1 单（attempts 次数 ≥ claim-1，无连发）。
    assert claim_calls - 1 <= len(attempts), (
        "each cross-pool claim must terminate its pass (one claim per attempt, "
        f"got {claim_calls - 1} claims over {len(attempts)} attempts)"
    )


def test_main_code_pool_claims_still_work_when_agent_pool_is_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#534 对照组：agent 池 0 不影响 code 池按自身预算领取——修复只堵
    「借池」，不收紧各池自己的正常消费。"""
    fake = FakeClient(tmp_path / "unused.tar.gz")
    claim_calls = 0
    release = threading.Event()
    filled = threading.Event()

    def claim(
        worker_id: str,
        max_concurrency: int | None = None,
        max_code_concurrency: int | None = None,
    ) -> dict | None:
        nonlocal claim_calls
        claim_calls += 1
        if claim_calls >= 2:
            filled.set()
            release.wait(timeout=5)
        payload = _claim(f"exec-{claim_calls}")
        # 前两单是 code 活（code 池预算 2）——executor 按 claim["kind"]
        # 分池；再往后停发（None 走空转）。
        if claim_calls <= 2:
            payload["kind"] = "code"
        return payload if claim_calls <= 2 else None

    def block_execution(  # type: ignore[no-untyped-def]
        client,
        claimed,
        work_root,
        environment,
        interval,
        stop,
        grace,
        status,
        uploads,
        slots,
        heartbeat_registry=None,
    ):
        release.wait(timeout=5)

    fake.claim = claim  # type: ignore[method-assign]
    monkeypatch.setattr(agent_worker, "run_execution", block_execution)
    updates = {
        "claim_enabled": True,
        "max_concurrency": 1,
        "max_code_concurrency": 2,
        "ramp_up": {"initial": 1, "step": 1, "interval_seconds": 60},
    }
    thread, handlers, result = _run_main(monkeypatch, tmp_path, fake, updates)
    assert filled.wait(timeout=5), "code pool should still claim its own budget"
    handlers[agent_worker.signal.SIGTERM]()
    release.set()
    thread.join(timeout=10)
    assert result == [0]
