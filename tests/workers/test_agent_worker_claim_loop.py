"""Claim-loop budget regressions (issue #534, split from test_agent_worker.py).

The per-pool claim-budget guard in worker/executor.py's claim loop: an
agent pool clamped to 0 (ramp-up tier / capacity full / upload backpressure)
must not borrow the code pool's budget to keep claiming agent executions —
and the one cross-pool claim that Host still grants must be SUBMITTED for
execution (heartbeat + result channels alive), not dropped to lease expiry.
The cross-pool suppression (PR #539 codex round 2) must reach the capacity
declaration on the claim call — the only channel Host's per-pool gate
(``active < declared``) listens to.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tests.workers.helpers import FakeClient, _claim, _run_main
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
        "ramp_up": {"initial": 1, "step": 1, "interval_seconds": 3600},
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
    """#534 对照组：另一池按自身预算正常领取不受影响——修复只堵「越池
    借预算」，不收紧各池自己的正常消费。场景里 agent 池在首单占住 tier
    后本地预算 0，code 池（预算 2）在同一 pass 照常消费满。"""
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
        "ramp_up": {"initial": 1, "step": 1, "interval_seconds": 3600},
    }
    thread, handlers, result = _run_main(monkeypatch, tmp_path, fake, updates)
    assert filled.wait(timeout=5), "code pool should still claim its own budget"
    handlers[agent_worker.signal.SIGTERM]()
    release.set()
    thread.join(timeout=10)
    assert result == [0]


class _FaithfulHost:
    """Host 分池记账的保真 fake（#501 声明容量 → needed_claim_kinds 门）。

    镜像 server/app/agent_broker/claim.py + agent_worker_capacity.py 的语义：
    每次 claim 按调用**声明的**容量记账，只发「active < 声明容量」的池的
    活；release_slot 归还名额（执行报果后 Host 记账面回落）。agent 供给
    默认无限（#534 的真实场景：Host 记账面与 Worker 本地爬坡预算脱钩，
    持续有余量就持续发）；``agent_supply`` 限量 / ``code_supply`` 供给
    code 活（默认无）。
    """

    def __init__(self, *, agent_supply: int | None = None, code_supply: int = 0) -> None:
        self._lock = threading.Lock()
        self._agent_active = 0
        self._agent_remaining = agent_supply
        self._code_active = 0
        self._code_remaining = code_supply
        self._serial = 0
        self.grants = 0
        self.declarations: list[tuple[int, int]] = []

    def claim(
        self,
        worker_id: str,
        max_concurrency: int | None = None,
        max_code_concurrency: int | None = None,
    ) -> dict | None:
        with self._lock:
            self.declarations.append((max_concurrency, max_code_concurrency))
            payload = None
            if (
                max_concurrency is not None
                and self._agent_active < max_concurrency
                and (self._agent_remaining is None or self._agent_remaining > 0)
            ):
                self._agent_active += 1
                if self._agent_remaining is not None:
                    self._agent_remaining -= 1
                payload = _claim(f"exec-{self._agent_active}")
            elif (
                self._code_remaining > 0
                and max_code_concurrency is not None
                and self._code_active < max_code_concurrency
            ):
                self._code_active += 1
                self._code_remaining -= 1
                payload = _claim(f"exec-code-{self._code_active}")
                payload["kind"] = "code"
            if payload is not None:
                self._serial += 1
                self.grants += 1
                return payload
            return None

    def release_slot(self, execution_id: str, lease_id: str) -> int:
        with self._lock:
            self._agent_active = max(0, self._agent_active - 1)
        return 204


def test_main_cross_pool_suppression_caps_declared_capacity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#534（PR #539 codex 二轮）：越池抑制必须压 claim 的**声明容量**。

    Host 按「active < 声明容量」分池发活（#501 声明的是目标容量，不随
    爬坡档位走）——本地预算只能 break 单个 pass，不压声明的话 Host 每个
    pass 都会再发一个越池的活。场景：target=8 + ramp initial=1（首单在
    跑后本地 agent 预算 0，Host 记账面 1 < 8 持续有余量）。旧形态（仅
    break）在本窗口内 running 爬到 8=声明容量，ramp-up 档位 1 被完全绕
    过；修后：抑制期间 agent 声明压到 min(活跃数, 目标)=1，Host 的分池
    门关闭不再发——running 钉在档位上；执行完成（release_slot 归还名
    额）后预算面恢复（avail > 0 解除），声明回声目标容量 8，恢复发活。"""
    fake = FakeClient(tmp_path / "unused.tar.gz")
    host = _FaithfulHost()
    fake.claim = host.claim  # type: ignore[method-assign]
    fake.release_slot = host.release_slot  # type: ignore[method-assign]
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
        # 执行报果后 Host 记账面回落（真实 run_execution 的收尾语义）。
        client.release_slot(claimed["execution_id"], claimed["lease_id"])

    submitted: list[object] = []

    def counting_execution(*args, **kwargs):  # type: ignore[no-untyped-def]
        submitted.append(args[1] if len(args) > 1 else None)
        return block_execution(*args, **kwargs)

    monkeypatch.setattr(agent_worker, "run_execution", counting_execution)
    # 拦截 claim.attempt 的预算快照（#534 验收面：agent 预算不再出现负值
    # 序列——原始 bug 借 code 预算把 agent 预算扣到 -31）。
    attempts: list[dict] = []
    monkeypatch.setattr(
        agent_worker_events,
        "note_claim_attempt",
        lambda worker_id, budget, depth, enabled: attempts.append(dict(budget)),
    )

    updates = {
        "claim_enabled": True,
        "max_concurrency": 8,
        "max_code_concurrency": 32,
        "ramp_up": {"initial": 1, "step": 1, "interval_seconds": 3600},
    }
    thread, handlers, result = _run_main(monkeypatch, tmp_path, fake, updates)
    try:
        deadline = time.monotonic() + 5
        while host.grants < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert host.grants >= 2, "cross-pool claim never granted"
        # 抑制窗口：首单在跑（release 未放行）、档位 3600s 不动。pass1
        # 正常领满 tier（budget 1→0）后越池一单（0→-1，触发抑制），共 2
        # grants；此后 Host 记账面持续有余量（2 < 8）——旧形态每个 pass
        # 再领一个直到 8。修后抑制期间 running 钉死在 2。
        window_end = time.monotonic() + 2.0
        while time.monotonic() < window_end:
            time.sleep(0.05)
        assert host.grants == 2, (
            f"suppressed pool must stop being granted (running climbed to "
            f"{host.grants} against ramp tier 1): declarations={host.declarations[:12]}"
        )
        # 抑制期间的声明：前两个是目标容量（正常领取 + 越池当单，抑制
        # 尚未发生），其后全部压到 min(活跃数, 目标)=2——这是 Host 分池
        # 门唯一听的通道。
        suppressed = host.declarations[2:]
        assert suppressed, "suppressed passes must keep polling (claim calls)"
        assert all(d == (2, 32) for d in suppressed), host.declarations[:20]
        # 预算验收面：整个窗口内 agent 预算从未为负。
        assert attempts, "claim.attempt events must have been emitted"
        assert all(a["agent"] >= 0 for a in attempts), [a for a in attempts if a["agent"] < 0]
        # 恢复面：执行完成后预算恢复（avail > 0 解除抑制），声明回声目标
        # 容量，Host 恢复发活——抑制不能变成永久性楔死。轮询等待而非在
        # 第三个 grant 时立即断言：两个报果的 release_slot 与 claim pass
        # 竞速——单核环境第一个 release_slot 后 pass 插入会看到
        # agent_active=1（tier 已满，抑制维持正确），该 pass 声明仍
        # (2,32) 且门 1<2 放行第三个 grant；两个报果都落账后下个 pass
        # 才 discard → (8,32)。
        release.set()
        deadline = time.monotonic() + 5
        while (8, 32) not in host.declarations[2:] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert host.grants >= 3, "suppression must lift once capacity frees up"
        assert (8, 32) in host.declarations[2:], (
            "declaration must return to target capacity after recovery"
        )
    finally:
        handlers[agent_worker.signal.SIGTERM]()
        release.set()
        thread.join(timeout=10)
    assert result == [0]
    # 无悬挂租约（codex P1 验收）：每个 grant 必被 submit——在 join 之后
    # 断言：claim 线程的 grants 计数与池线程 counting wrapper 的 submitted
    # 计数跨线程，刚 grant 的执行可能还在 submit 队列里（wrapper 未跑），
    # 主循环 join + 池收尾后必然追平。
    assert len(submitted) == host.grants, (
        f"every granted execution must be submitted: {len(submitted)} submitted "
        f"vs {host.grants} grants"
    )


def test_main_normal_fill_does_not_suppress_or_clip_declaration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#534（PR #539 review P2-1）：正常领满 ≠ 越池，不得进抑制。

    守卫历史：1fbfe8a0 在 decrement 之前判（只拦真越池）；67e9e52a 移到
    decrement 之后修复悬挂租约时触发面静默改宽到「<= 0」——预算 1 →
    正常领取 → 0 也命中；ff5910f2 在其上挂了 pool_deferred，正常领满
    也进抑制：ramp 满档窗口（tier=1、target=8，最常见状态）声明容量
    跌到 min(活跃数, 目标)=档位值，且随补位在档位↔目标间振荡——违反
    #501「声明不随档位抖」。修后（< 0 才是真越池）：满档窗口声明恒为
    目标容量，双池 pass 语义也与 1fbfe8a0 对齐（领满不 break 整个
    pass）。场景：tier=1 + agent 供给恰好 1 单（首单正常领满预算
    1→0，供给同步耗尽——无越池面）+ code 池满额 2。"""
    fake = FakeClient(tmp_path / "unused.tar.gz")
    host = _FaithfulHost(agent_supply=1, code_supply=2)
    fake.claim = host.claim  # type: ignore[method-assign]
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

    monkeypatch.setattr(agent_worker, "run_execution", block_execution)
    updates = {
        "claim_enabled": True,
        "max_concurrency": 8,
        "max_code_concurrency": 2,
        "ramp_up": {"initial": 1, "step": 1, "interval_seconds": 3600},
    }
    thread, handlers, result = _run_main(monkeypatch, tmp_path, fake, updates)
    try:
        deadline = time.monotonic() + 5
        while host.grants < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        # agent tier 1 正常领满（预算 1→0，供给同步耗尽，无越池）+
        # code 池领满 2——全程不进抑制。
        assert host.grants == 3, (
            f"both pools should fill their budgets (1 agent + 2 code), got {host.grants}"
        )
        # 满档窗口观察：正常领满不得触发抑制——每个声明都是目标容量
        # （8, 2），agent 行值不随档位抖（#501）。
        window_end = time.monotonic() + 1.5
        while time.monotonic() < window_end:
            time.sleep(0.05)
        assert all(d == (8, 2) for d in host.declarations), host.declarations[:20]
        assert host.grants == 3, "normal fill must not keep claiming past its budgets"
    finally:
        handlers[agent_worker.signal.SIGTERM]()
        release.set()
        thread.join(timeout=10)
    assert result == [0]
