"""Concurrency-ceiling contract tests (issue #657).

The per-Worker concurrency ceiling is a TWO-SIDED contract guard: the
Worker validates its config locally (``worker/runtime/controls``) and the
Host enforces the same bound at registration and at every claim
re-declaration. Before #657 both sides hardcoded 1024 independently — a
single-sided bump would strand Workers at 422s. Both sides now reference
``shared.concurrency_limits.MAX_DYNAMIC_CONCURRENCY``; these tests pin the
equality (worker local == every Host contract field) so the pair cannot
drift again, plus the 2048 acceptance round-trips.
"""

from __future__ import annotations

import pydantic.fields
import pytest

from server.app.routes.agent_worker_claim_contracts import ClaimAgentExecutionRequest
from server.app.routes.agent_workers_contracts import RegisterAgentWorkerRequest
from shared.concurrency_limits import MAX_DYNAMIC_CONCURRENCY
from worker.runtime.controls import MAX_DYNAMIC_CONCURRENCY as WORKER_CEILING

pytestmark = pytest.mark.no_db


def test_worker_and_host_share_one_ceiling() -> None:
    assert WORKER_CEILING == MAX_DYNAMIC_CONCURRENCY == 2048


def test_every_host_contract_field_binds_the_shared_ceiling() -> None:
    """Registration + claim contracts must all reference the SAME constant —
    a fresh le=<literal> reintroduction fails here (the metadata carries the
    bound, and the bound must equal the shared ceiling)."""
    fields: list[tuple[str, int]] = []
    for model, names in (
        (RegisterAgentWorkerRequest, ("max_concurrency", "max_code_concurrency")),
        (
            ClaimAgentExecutionRequest,
            ("max_concurrency", "max_code_concurrency", "limit", "agent_limit", "code_limit"),
        ),
    ):
        for name in names:
            field = model.model_fields[name]
            # pydantic packs Field(gt/ge/le) constraints into annotated
            # metadata; walk to find the le bound wherever it landed.
            bound = _le_bound(field)
            assert bound is not None, f"{model.__name__}.{name} lost its le bound"
            fields.append((f"{model.__name__}.{name}", bound))
    assert fields and all(bound == MAX_DYNAMIC_CONCURRENCY for _name, bound in fields), fields


def _le_bound(field: pydantic.fields.FieldInfo) -> int | None:
    for meta in field.metadata:
        le = getattr(meta, "le", None)
        if le is not None:
            return le
    return None


def test_worker_local_validation_accepts_the_ceiling() -> None:
    from worker.runtime.controls import validate_claim_controls

    validate_claim_controls(MAX_DYNAMIC_CONCURRENCY, True)  # boundary legal
    with pytest.raises(ValueError):
        validate_claim_controls(MAX_DYNAMIC_CONCURRENCY + 1, True)  # one past


def test_host_registration_accepts_2048() -> None:
    payload = RegisterAgentWorkerRequest(
        worker_id="w-2048",
        name="W",
        runtimes=["velites"],
        max_concurrency=2048,
        max_code_concurrency=2048,
        protocol_version=1,
    )
    assert payload.max_concurrency == MAX_DYNAMIC_CONCURRENCY
    with pytest.raises(ValueError):
        RegisterAgentWorkerRequest(
            worker_id="w-over",
            name="W",
            runtimes=["velites"],
            max_concurrency=MAX_DYNAMIC_CONCURRENCY + 1,
            protocol_version=1,
        )


def test_relay_shard_admission_covers_the_ceiling() -> None:
    """#657 隐性假设复核：relay 的跨拍分片准入上限必须 ≥ 声明上限按分片
    大小的向上取整（2048/64 = 32）——上限放宽后 16 会饿死满档 worker 的
    心跳分片（每拍有分片被拒 = 未知轮，回到 #591 的排空语义）。"""
    from worker.relay_shards import RELAY_BEAT_SHARD
    from worker.relay_thread_limiter import MAX_INFLIGHT_SHARDS

    assert MAX_INFLIGHT_SHARDS >= -(-MAX_DYNAMIC_CONCURRENCY // RELAY_BEAT_SHARD)


def test_load_shedding_decouples_from_the_concurrency_ceiling() -> None:
    """#657 隐性假设复核：load 回压按核数判定，与并发档位无耦合——上限
    放宽到 2048 不改变回压行为（同一核数下阈值恒定）。"""
    from worker.load_shedding import LoadShedder

    shedder_1024 = LoadShedder(1024, log=lambda _m: None)
    shedder_2048 = LoadShedder(2048, log=lambda _m: None)
    budget = {"agent": 64, "code": 0}
    # 同预算下两档位的回压判定一致（核数未变，档位不参与判定）。
    assert shedder_1024.shed(dict(budget)) == shedder_2048.shed(dict(budget))
