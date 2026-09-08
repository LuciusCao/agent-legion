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
        self, worker_id, max_concurrency, max_code_concurrency, *, limit, agent_limit, code_limit
    ):  # type: ignore[no-untyped-def]
        self.calls.append(
            {
                "worker_id": worker_id,
                "max_concurrency": max_concurrency,
                "max_code_concurrency": max_code_concurrency,
                "limit": limit,
                "agent_limit": agent_limit,
                "code_limit": code_limit,
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
