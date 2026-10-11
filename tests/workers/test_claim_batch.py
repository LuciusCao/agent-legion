"""Unit tests for the worker-side batch claim pass (issue #546).

Covers the sizing fold (``batch_request``), the hot-reloadable
``claim_batch_limit`` loader, and the ``drain_budget`` loop semantics: a
delivered batch is submitted in full, an empty batch ends the pass, and a
cross-pool over-claim (#534) is accounted AFTER the batch and ends the pass
(the pre-#546 break-after-submit semantics generalized to batches).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from worker.claim_batch import (
    DEFAULT_CLAIM_BATCH_LIMIT,
    MAX_CLAIM_BATCH_LIMIT,
    ClaimRunContext,
    batch_request,
    drain_budget,
    load_claim_batch_limit,
)

pytestmark = pytest.mark.no_db


def _write_config(tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "worker.yaml"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


class TestLoadClaimBatchLimit:
    def test_default_when_absent(self, tmp_path: Path) -> None:
        assert load_claim_batch_limit(_write_config(tmp_path, {})) == DEFAULT_CLAIM_BATCH_LIMIT

    def test_valid_value(self, tmp_path: Path) -> None:
        assert load_claim_batch_limit(_write_config(tmp_path, {"claim_batch_limit": 64})) == 64

    @pytest.mark.parametrize("value", [0, -1, True, MAX_CLAIM_BATCH_LIMIT + 1, "32", 2.5])
    def test_invalid_values_raise(self, tmp_path: Path, value: object) -> None:
        with pytest.raises(ValueError, match="claim_batch_limit"):
            load_claim_batch_limit(_write_config(tmp_path, {"claim_batch_limit": value}))


class TestBatchRequest:
    def test_each_pool_capped_by_batch_limit(self) -> None:
        assert batch_request({"agent": 100, "code": 100}, 32) == (32, 32, 32)

    def test_total_caps_at_batch_limit(self) -> None:
        assert batch_request({"agent": 20, "code": 20}, 32) == (32, 20, 20)

    def test_small_budgets_pass_through(self) -> None:
        assert batch_request({"agent": 2, "code": 1}, 32) == (3, 2, 1)

    def test_negative_pool_contributes_zero(self) -> None:
        # #534：越池的池当批即止（agent_limit=0），不拖到下个 pass。
        assert batch_request({"agent": -3, "code": 5}, 32) == (5, 0, 5)

    def test_zero_both(self) -> None:
        assert batch_request({"agent": 0, "code": 0}, 32) == (0, 0, 0)


class _FakeBatchClient:
    """Scripted batch Host: each entry is one call's answer."""

    def __init__(self, script: list[list[dict]]) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def claim_batch(
        self,
        worker_id,
        max_concurrency,
        max_code_concurrency,
        *,
        limit,
        agent_limit,
        code_limit,
        node_concurrency_limits=None,
    ):  # type: ignore[no-untyped-def]
        self.calls.append(
            {
                "worker_id": worker_id,
                "max_concurrency": max_concurrency,
                "max_code_concurrency": max_code_concurrency,
                "limit": limit,
                "agent_limit": agent_limit,
                "code_limit": code_limit,
                "node_concurrency_limits": node_concurrency_limits,
            }
        )
        return self.script.pop(0)


def _ctx(client: _FakeBatchClient) -> tuple[ClaimRunContext, dict[str, int], list[dict]]:
    submitted: list[dict] = []
    pool_deferred: set[str] = set()
    ctx = ClaimRunContext(
        client=client,
        worker_id="w1",
        pool=None,
        run_args=(),
        run_tail=(),
        heartbeat_registry=None,
        active=set(),
        active_kinds={},
        pool_deferred=pool_deferred,
        stop=threading.Event(),
    )
    return ctx, pool_deferred, submitted


def _submitter(collector: list[dict], budget: dict[str, int]):  # type: ignore[no-untyped-def]
    def submit(claim: dict) -> None:
        kind = "code" if str(claim.get("kind")) == "code" else "agent"
        budget[kind] -= 1
        collector.append(claim)

    return submit


class TestDrainBudget:
    def test_node_limits_reach_the_claim_payload(self) -> None:
        """#1158：热更读到的节点上限映射随每次 claim 声明透传到 client。"""
        budget = {"agent": 1, "code": 0}
        ctx, _pool_deferred, submitted = _ctx(_FakeBatchClient([]))
        ctx.node_limits = {"heavy": 1}
        ctx.client.script = [[{"execution_id": "e0", "kind": "agent", "node_key": "generate"}]]

        drain_budget(
            ctx,
            budget,
            {"agent": 10, "code": 0},
            32,
            0,
            True,
            _submitter(submitted, budget),
        )

        assert ctx.client.calls[0]["node_concurrency_limits"] == {"heavy": 1}

    def test_one_batch_fills_budget(self) -> None:
        budget = {"agent": 3, "code": 0}
        ctx, pool_deferred, submitted = _ctx(_FakeBatchClient([]))
        claims = [{"execution_id": f"e{i}", "kind": "agent"} for i in range(3)]
        ctx.client.script = [claims]

        claimed, _ = drain_budget(
            ctx, budget, {"agent": 10, "code": 0}, 32, 0, True, _submitter(submitted, budget)
        )

        assert claimed is True
        assert submitted == claims
        assert budget == {"agent": 0, "code": 0}
        assert pool_deferred == set()
        assert ctx.client.calls[0]["limit"] == 3
        assert ctx.client.calls[0]["agent_limit"] == 3

    def test_multiple_batches_until_empty(self) -> None:
        budget = {"agent": 3, "code": 0}
        ctx, _, submitted = _ctx(_FakeBatchClient([]))
        ctx.client.script = [
            [{"execution_id": "e1", "kind": "agent"} for _ in range(2)],
            [{"execution_id": "e3", "kind": "agent"}],
            [],
        ]

        claimed, _ = drain_budget(
            ctx, budget, {"agent": 10, "code": 0}, 32, 0, True, _submitter(submitted, budget)
        )

        assert claimed is True
        assert [c["execution_id"] for c in submitted] == ["e1", "e1", "e3"]
        assert budget["agent"] == 0
        # 三个调用：批 2 条 → 批 1 条 → 预算耗尽即停（第三次调用不发生）。
        assert len(ctx.client.calls) == 2

    def test_empty_first_batch_reports_unclaimed(self) -> None:
        budget = {"agent": 5, "code": 0}
        ctx, _, submitted = _ctx(_FakeBatchClient([[]]))

        claimed, _ = drain_budget(
            ctx, budget, {"agent": 10, "code": 0}, 32, 0, True, _submitter(submitted, budget)
        )

        assert claimed is False
        assert submitted == []

    def test_over_claim_defers_pool_and_ends_pass(self) -> None:
        """#534/#535 批形态：Host 竞态超发的整批照单全收（全部 submit）、批后
        记抑制并终止本 pass——负预算池不得继续折算下一轮批大小。"""
        budget = {"agent": 1, "code": 4}
        ctx, pool_deferred, submitted = _ctx(_FakeBatchClient([]))
        ctx.client.script = [
            [
                {"execution_id": "e1", "kind": "agent"},
                {"execution_id": "e2", "kind": "agent"},
                {"execution_id": "c1", "kind": "code"},
            ],
            # 若循环不破，这个脚本项会被消费——断言它不被消费。
            [{"execution_id": "c2", "kind": "code"}],
        ]

        claimed, _ = drain_budget(
            ctx, budget, {"agent": 10, "code": 4}, 32, 0, True, _submitter(submitted, budget)
        )

        assert claimed is True
        assert len(submitted) == 3, "delivered batch must be submitted in full"
        assert budget == {"agent": -1, "code": 3}
        assert pool_deferred == {"agent"}
        assert len(ctx.client.calls) == 1, "over-claim ends the pass after the batch"

    def test_stop_event_breaks_before_request(self) -> None:
        budget = {"agent": 1, "code": 0}
        ctx, _, submitted = _ctx(_FakeBatchClient([]))
        ctx.stop.set()

        claimed, _ = drain_budget(
            ctx, budget, {"agent": 10, "code": 0}, 32, 0, True, _submitter(submitted, budget)
        )

        assert claimed is False
        assert ctx.client.calls == []
        assert submitted == []

    def test_batch_limit_one_degenerates_to_per_claim_requests(self) -> None:
        """claim_batch_limit=1 = 事故降级旋钮（退回 0.7.3 的逐条领取形态）：
        每个请求 limit/agent_limit/code_limit 都不超过 1，预算照常消费。"""
        budget = {"agent": 3, "code": 0}
        ctx, pool_deferred, submitted = _ctx(_FakeBatchClient([]))
        ctx.client.script = [
            [{"execution_id": "e1", "kind": "agent"}],
            [{"execution_id": "e2", "kind": "agent"}],
            [{"execution_id": "e3", "kind": "agent"}],
        ]

        claimed, _ = drain_budget(
            ctx, budget, {"agent": 10, "code": 0}, 1, 0, True, _submitter(submitted, budget)
        )

        assert claimed is True
        assert len(submitted) == 3
        assert all(
            call["limit"] == 1 and call["agent_limit"] == 1 and call["code_limit"] == 0
            for call in ctx.client.calls
        )
        assert pool_deferred == set()


class TestClaimBatchLimitConfigApi:
    """#546 复审 P1：claim_batch_limit 必须走通控制台/PUT /api/config 的
    热更通道——它是 batch claim 唯一的事故降级旋钮（调回 1 = 逐条领取）。"""

    def _app(self, tmp_path: Path):  # type: ignore[no-untyped-def]
        from fastapi.testclient import TestClient

        from worker.service import create_app
        from worker.supervisor import WorkerConfigStore, validate_config

        store = WorkerConfigStore(tmp_path / "state")
        store.write(
            validate_config(
                {
                    "host_url": "http://host.test:8000/",
                    "worker_id": "worker-1",
                    "max_concurrency": 3,
                    "register_token_file": "/run/secrets/register-token",
                }
            )
        )

        class FakeSupervisor:
            """PUT /api/config 全链路需要的最小 supervisor 面（status 进响应）。"""

            def __init__(self, store: WorkerConfigStore) -> None:
                self.store = store
                self.restarts = 0

            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def restart(self) -> None:
                self.restarts += 1

            def status(self) -> dict[str, Any]:
                return {"service": "running", "worker_running": True}

        supervisor = FakeSupervisor(store)
        app = create_app(supervisor, tmp_path)
        headers = {"Authorization": f"Bearer {store.control_token()}"}
        return store, supervisor, TestClient(app, base_url="http://127.0.0.1"), headers

    def test_hot_update_without_restart(self, tmp_path: Path) -> None:
        store, supervisor, client, headers = self._app(tmp_path)
        with client:
            response = client.put("/api/config", json={"claim_batch_limit": 8}, headers=headers)
            # 无关字段的保存不得丢该键（merge 语义，不是整体替换）。
            other = client.put("/api/config", json={"max_concurrency": 5}, headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["restarted"] is False, "claim_batch_limit 是热更字段"
        assert response.json()["config"]["claim_batch_limit"] == 8
        assert supervisor.restarts == 0
        assert other.json()["config"]["claim_batch_limit"] == 8

    def test_invalid_value_rejected_422(self, tmp_path: Path) -> None:
        store, _, client, headers = self._app(tmp_path)
        with client:
            response = client.put("/api/config", json={"claim_batch_limit": 0}, headers=headers)

        assert response.status_code == 422
        assert store.read()["claim_batch_limit"] == 32, "校验失败不得半应用（保持默认值）"


def test_lane_spawn_error_mid_batch_logs_unsubmitted_and_propagates(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """#1051：批内第二条提交撞线程耗尽（LaneSpawnError）——已提交的第一条
    保留，当前条与本批余下逐条按 execution_id 记日志（交租约过期由 Host
    重排队），异常原样上抛给 executor 的专用退避臂，本 pass 不再发起 claim。"""
    from worker.execution.execution_lane import LaneSpawnError

    budget = {"agent": 3, "code": 0}
    claims = [{"execution_id": f"e{i}", "kind": "agent"} for i in range(3)]
    ctx, _, submitted = _ctx(_FakeBatchClient([claims]))
    collect = _submitter(submitted, budget)

    def submit(claim: dict) -> None:
        if claim["execution_id"] == "e1":
            raise LaneSpawnError("execution lane thread start failed: can't start new thread")
        collect(claim)

    with pytest.raises(LaneSpawnError):
        drain_budget(ctx, budget, {"agent": 10, "code": 0}, 32, 0, True, submit)

    assert [c["execution_id"] for c in submitted] == ["e0"]
    assert len(ctx.client.calls) == 1
    out = capsys.readouterr().out
    assert "left to lease expiry: e1, e2" in out
    assert ctx.lane_probe is True


def test_lane_exhaustion_clamps_each_pass_to_one_probe_until_submit_succeeds() -> None:
    """#1051 熔断：撞 LaneSpawnError 后，耗尽期间每个退避后的 pass 至多领 1
    条（每次丢弃都消耗该执行一次 Host 重排次数，满批再丢会成批烧光
    requeue_limit 判败节点）；首次 submit 成功即解除，同一 pass 后续轮次
    与之后的 pass 恢复满额批申请。"""
    from worker.execution.execution_lane import LaneSpawnError

    exhausted = True
    submitted: list[str] = []
    budget = {"agent": 5, "code": 0}

    def submit(claim: dict) -> None:
        if exhausted:
            raise LaneSpawnError("execution lane thread start failed: can't start new thread")
        budget["agent"] -= 1
        submitted.append(claim["execution_id"])

    def claims(prefix: str, n: int) -> list[dict]:
        return [{"execution_id": f"{prefix}{i}", "kind": "agent"} for i in range(n)]

    ctx, _, _ = _ctx(_FakeBatchClient([claims("a", 5)]))
    with pytest.raises(LaneSpawnError):
        drain_budget(ctx, {"agent": 5, "code": 0}, {"agent": 10, "code": 0}, 32, 0, True, submit)
    assert ctx.client.calls[-1]["limit"] == 5
    assert ctx.lane_probe is True

    # 仍耗尽：退避后的两个 pass 各只探测 1 条。
    for prefix in ("b", "c"):
        ctx.client.script = [claims(prefix, 1)]
        with pytest.raises(LaneSpawnError):
            drain_budget(
                ctx, {"agent": 5, "code": 0}, {"agent": 10, "code": 0}, 32, 0, True, submit
            )
        assert ctx.client.calls[-1]["limit"] == 1
        assert ctx.client.calls[-1]["agent_limit"] == 1
    assert submitted == []

    # 资源恢复：探测 1 条成功 → 同一 pass 下一轮即恢复满额（剩余预算 4）。
    exhausted = False
    budget = {"agent": 5, "code": 0}
    calls_before = len(ctx.client.calls)
    ctx.client.script = [claims("d", 1), claims("e", 4)]
    claimed, _ = drain_budget(ctx, budget, {"agent": 10, "code": 0}, 32, 0, True, submit)
    assert claimed is True
    assert [call["limit"] for call in ctx.client.calls[calls_before:]] == [1, 4]
    assert ctx.lane_probe is False
    assert submitted == ["d0", "e0", "e1", "e2", "e3"]

    # 之后的 pass 直接满额。
    budget = {"agent": 5, "code": 0}
    ctx.client.script = [claims("f", 5)]
    drain_budget(ctx, budget, {"agent": 10, "code": 0}, 32, 0, True, submit)
    assert ctx.client.calls[-1]["limit"] == 5
