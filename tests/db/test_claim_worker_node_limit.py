"""Worker 节点级并发上限（issue #1158）：每台机器按裸 node_key 的执行容量。

两层并发模型的第二层：workspace_node_limits（#1149）是跨全部机器合并计数
的 workspace 全局上限；本文件钉住的是 (worker_id, node_key) 的机器资源保
护层——Worker 每次 claim 重声明 {node_key: N}（sync_declared_capacity 热
同步，无需重注册），Host 在 evaluate_candidate 里按本机 claimed 请求行计
数强制（不分 kind），超限 skip（worker_node_limit_full）留队列。

验收面对应：

1. 弱机器声明 {heavy: 1}，两台机器并发 claim 同节点（workspace 上限 4）：
   弱机器第二个 skip 留队列，强机器（未声明）不受影响——单机单节点有效
   上限 = min(worker 声明上限, workspace 全局预算余量）由两道独立的门叠加
   得出，无联动；
2. 声明热生效：claim 声明即同步进 agent_workers 库存列并在同一 claim 内
   生效；None（不声明）保留库存值，显式 {} 清空（无限制）；
3. 未声明 / 空 map 时行为与现状完全一致（同节点两请求都可领取，零回归面）；
4. 计数不分 kind：agent 与 code 的 claimed 行都占本机该节点的额度；
5. 批 claim 同节点多候选：读相预过滤按「在跑 + 本批已选」记账，第一个
   入选后第二个即被拒，批保留第一个；
6. 读相预过滤（PR #1229 R1 P1）：已满节点排在队首时，读相
   （claim_batch_select）按 在跑快照 + 本批已选 记账拒选，一批领走其后
   的可执行节点而非返回空批——写相 ``worker_node_admits`` 保持权威重
   校验一行不动；
7. 写相权威门直调（R1 复验补钉）：读相未声明、写相才同步到声明的形态
   （滚动升级窗口竞态），第二个候选被 ``worker_node_admits`` 拒并留队列
   ——预过滤上线后写相拒绝路径的唯一覆盖。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_broker.claim_batch import claim_batch_with_retry
from server.app.agent_broker.claim_batch_select import select_batch_candidates
from server.app.agent_broker.claim_batch_tx import claim_batch_in_transaction
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.db.transaction import write_transaction
from shared.protocol import PROTOCOL_VERSION
from tests.helpers.agent_worker_api import seed_request
from tests.postgres_support import TEST_DATABASE_URL


def _register_worker(worker_id: str) -> None:
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id=worker_id,
        name=worker_id,
        runtimes=["pi"],
        capabilities=["package"],
        models=[{"provider": "gateway", "model": "test-model", "runtime": "pi"}],
        max_concurrency=10,
        max_code_concurrency=10,
        labels={"arch": "arm64"},
        protocol_version=PROTOCOL_VERSION,
    )


def _seed_code_job(job_db, workspace_id: str, job_id: str, node_key: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'Test') on conflict(id) do nothing",
            (workspace_id,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values (%s, %s, 'question', %s)",
            (job_id, workspace_id, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, %s)", (job_id, node_key))


def _set_node_limit(job_db, workspace_id: str, node_key: str, limit: int) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_limits(workspace_id, node_key, concurrency_limit)"
            " values (%s, %s, %s)"
            " on conflict(workspace_id, node_key) do update set"
            " concurrency_limit=excluded.concurrency_limit",
            (workspace_id, node_key, limit),
        )


def _enqueue_code(
    job_db,
    workspace_id: str,
    job_id: str,
    node_key: str,
    *,
    order: int = 0,
) -> str:
    execution_id = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent).enqueue(
        AgentExecutionRequest(
            workspace_id=workspace_id,
            job_id=job_id,
            workflow_key=workspace_id,
            node_key=node_key,
            agent_id="package",
            agent_definition_hash="codehash",
            manifest={
                "kind": "code",
                "capability": "package",
                "code_hash": "abc123",
                "job_id": job_id,
                "log_path": f"logs/{job_id}-{node_key}.log",
                "config": {"mode": "fast"},
            },
            kind="code",
        )
    )
    assert execution_id is not None
    # queued_at 钉死队列序（同毫秒入队时 current_timestamp 无序）。
    queued_at = datetime(2026, 10, 1, 9, 0, tzinfo=UTC) + timedelta(seconds=order)
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set queued_at=%s where execution_id=%s",
            (queued_at, execution_id),
        )
    return execution_id


def _request_state(job_db, execution_id: str) -> str:
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id=%s",
            (execution_id,),
        ).fetchone()
    assert row is not None
    return str(row["state"])


def _stored_node_limits(job_db, worker_id: str) -> dict[str, Any]:
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select node_concurrency_limits_json from agent_workers where worker_id=%s",
            (worker_id,),
        ).fetchone()
    assert row is not None
    return dict(json.loads(str(row["node_concurrency_limits_json"])))


def test_weak_worker_capped_while_strong_worker_claims_same_node(job_db) -> None:
    """验收 1：弱机器声明 {heavy: 1}，workspace 全局上限 4——弱机器第二个
    同节点请求 skip（worker_node_limit_full）留队列；强机器未声明、不受
    影响照常领取（workspace 层的 lease 计数 1 < 4 放行）。"""
    workspace_id, node_key = "ws-1158-a", "heavy"
    _seed_code_job(job_db, workspace_id, "job-a1", node_key)
    _seed_code_job(job_db, workspace_id, "job-a2", node_key)
    _set_node_limit(job_db, workspace_id, node_key, 4)
    _enqueue_code(job_db, workspace_id, "job-a1", node_key, order=0)
    second = _enqueue_code(job_db, workspace_id, "job-a2", node_key, order=1)
    _register_worker("worker-1158-weak")
    _register_worker("worker-1158-strong")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    weak1 = claim_batch_with_retry(
        pool,
        "worker-1158-weak",
        None,
        None,
        limit=1,
        code_limit=1,
        declared_node_limits={node_key: 1},
    )
    assert [claim.job_id for claim in weak1.claims] == ["job-a1"]

    weak2 = claim_batch_with_retry(
        pool,
        "worker-1158-weak",
        None,
        None,
        limit=1,
        code_limit=1,
        declared_node_limits={node_key: 1},
    )
    assert weak2.claims == ()
    assert weak2.skip_reasons.get("worker_node_limit_full") == 1
    assert _request_state(job_db, second) == "queued"  # 留队列，未被取消

    strong = claim_batch_with_retry(pool, "worker-1158-strong", None, None, limit=1, code_limit=1)
    assert [claim.job_id for claim in strong.claims] == ["job-a2"]
    assert _request_state(job_db, second) == "claimed"


def test_node_limit_declaration_hot_syncs_and_enforces_immediately(job_db) -> None:
    """验收 2：声明热生效——claim 携带的映射同步进 agent_workers 库存列并
    在同一 claim 的判定里生效；None（不声明）保留库存值，显式 {} 清空。"""
    workspace_id, node_key = "ws-1158-b", "heavy"
    for index in range(3):
        _seed_code_job(job_db, workspace_id, f"job-b{index}", node_key)
    # job-b2 刻意延后在 round2 之后入队：读相预过滤会对队列里每个已满节点
    # 候选计一次 skip（直方图语义），round2 的 ==1 断言要求当时队列里只有
    # job-b1 一个 heavy 候选。
    _enqueue_code(job_db, workspace_id, "job-b0", node_key, order=0)
    _enqueue_code(job_db, workspace_id, "job-b1", node_key, order=1)
    _register_worker("worker-1158-b")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    round1 = claim_batch_with_retry(
        pool,
        "worker-1158-b",
        None,
        None,
        limit=1,
        code_limit=1,
        declared_node_limits={node_key: 1},
    )
    assert [claim.job_id for claim in round1.claims] == ["job-b0"]
    assert _stored_node_limits(job_db, "worker-1158-b") == {node_key: 1}

    # None = 不声明：库存值保留并继续生效（计数 1 >= 1 → skip）。
    round2 = claim_batch_with_retry(pool, "worker-1158-b", None, None, limit=1, code_limit=1)
    assert round2.claims == ()
    assert round2.skip_reasons.get("worker_node_limit_full") == 1
    assert _stored_node_limits(job_db, "worker-1158-b") == {node_key: 1}

    _enqueue_code(job_db, workspace_id, "job-b2", node_key, order=2)

    # 显式 {} = 清空：同一 claim 即按新映射放行，库存列同步为 {}。
    round3 = claim_batch_with_retry(
        pool, "worker-1158-b", None, None, limit=1, code_limit=1, declared_node_limits={}
    )
    assert [claim.job_id for claim in round3.claims] == ["job-b1"]
    assert _stored_node_limits(job_db, "worker-1158-b") == {}

    # 收紧→放宽的热更同样在声明的同一 claim 生效：计数 2 < 3 → 放行。
    round4 = claim_batch_with_retry(
        pool,
        "worker-1158-b",
        None,
        None,
        limit=1,
        code_limit=1,
        declared_node_limits={node_key: 3},
    )
    assert [claim.job_id for claim in round4.claims] == ["job-b2"]
    assert _stored_node_limits(job_db, "worker-1158-b") == {node_key: 3}


def test_undeclared_and_empty_map_stay_unlimited(job_db) -> None:
    """验收 3（零回归面）：未声明（旧 Worker）与显式空 map 都不设限——
    同节点两个请求照常全部领取，skip_reasons 无 worker_node_limit_full。"""
    workspace_id, node_key = "ws-1158-c", "package"
    for index in range(3):
        _seed_code_job(job_db, workspace_id, f"job-c{index}", node_key)
        _enqueue_code(job_db, workspace_id, f"job-c{index}", node_key, order=index)
    _register_worker("worker-1158-legacy")
    _register_worker("worker-1158-empty")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    legacy = claim_batch_with_retry(pool, "worker-1158-legacy", None, None, limit=2, code_limit=2)
    assert [claim.job_id for claim in legacy.claims] == ["job-c0", "job-c1"]
    assert "worker_node_limit_full" not in legacy.skip_reasons

    empty = claim_batch_with_retry(
        pool, "worker-1158-empty", None, None, limit=1, code_limit=1, declared_node_limits={}
    )
    assert [claim.job_id for claim in empty.claims] == ["job-c2"]
    assert "worker_node_limit_full" not in empty.skip_reasons
    assert _stored_node_limits(job_db, "worker-1158-legacy") == {}
    assert _stored_node_limits(job_db, "worker-1158-empty") == {}


def test_worker_node_limit_counts_both_kinds(job_db) -> None:
    """计数不分 kind：机器资源保护覆盖任何在本机烧 CPU 的执行——同
    node_key 的 agent 请求被认领后，同 key 的 code 请求撞 {key: 1} skip。"""
    workspace_id, node_key = "ws-1158-d", "review"
    seed_request(job_db, job_id="job-d-agent", workspace_id=workspace_id, node_key=node_key)
    _seed_code_job(job_db, workspace_id, "job-d-code", node_key)
    _enqueue_code(job_db, workspace_id, "job-d-code", node_key)
    _register_worker("worker-1158-d")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    # 第一轮只开 agent 池：agent 请求被认领，占去本机该节点 1 个额度。
    round1 = claim_batch_with_retry(
        pool,
        "worker-1158-d",
        None,
        None,
        limit=1,
        agent_limit=1,
        code_limit=0,
        declared_node_limits={node_key: 1},
    )
    assert [claim.job_id for claim in round1.claims] == ["job-d-agent"]

    # 第二轮只开 code 池：同 key 的 code 请求按不分 kind 的计数 skip。
    round2 = claim_batch_with_retry(
        pool,
        "worker-1158-d",
        None,
        None,
        limit=1,
        agent_limit=0,
        code_limit=1,
        declared_node_limits={node_key: 1},
    )
    assert round2.claims == ()
    assert round2.skip_reasons.get("worker_node_limit_full") == 1


def test_batch_second_candidate_same_node_skips(job_db) -> None:
    """批 claim（#546/#555）同节点两候选：读相预过滤按「在跑 + 本批已选」
    记账（#1158 R1）——第一个入选后第二个即被拒（worker_node_limit_full，
    读相产生），批保留第一个 claim（skip-and-continue）。批写相的锁内
    权威重校验（worker_node_admits）由用例 7 直调覆盖。"""
    workspace_id, node_key = "ws-1158-e", "heavy"
    _seed_code_job(job_db, workspace_id, "job-e1", node_key)
    _seed_code_job(job_db, workspace_id, "job-e2", node_key)
    first = _enqueue_code(job_db, workspace_id, "job-e1", node_key, order=0)
    second = _enqueue_code(job_db, workspace_id, "job-e2", node_key, order=1)
    _register_worker("worker-1158-e")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    outcome = claim_batch_with_retry(
        pool,
        "worker-1158-e",
        None,
        None,
        limit=2,
        code_limit=2,
        declared_node_limits={node_key: 1},
    )

    assert [claim.job_id for claim in outcome.claims] == ["job-e1"]
    assert outcome.skip_reasons.get("worker_node_limit_full") == 1
    assert _request_state(job_db, first) == "claimed"
    assert _request_state(job_db, second) == "queued"


def test_read_phase_prefilter_serves_nodes_behind_a_full_head(job_db) -> None:
    """用例 6（PR #1229 R1 P1 饿死修复钉）：弱机 {heavy: 1} 已有一个 heavy
    在跑；队列里两个 heavy 在前、light 在后——批 limit=2 时读相预过滤
    逐一拒选两个已满的 heavy（skip 各计一次 worker_node_limit_full），
    同一批领走后面的 light；修复前两个批槽全被 heavy 候选烧掉、写相
    拒收返回空批，后续轮询重复，light 被饿死到 heavy 完成。写相
    ``worker_node_admits`` 仍是权威重校验。"""
    workspace_id = "ws-1158-f"
    for job_id, node_key in (
        ("job-f-running", "heavy"),
        ("job-f-heavy-a", "heavy"),
        ("job-f-heavy-b", "heavy"),
        ("job-f-light", "light"),
    ):
        _seed_code_job(job_db, workspace_id, job_id, node_key)
    _register_worker("worker-1158-f")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    # 占满额度：先领走一个 heavy（声明 {heavy: 1}）。
    _enqueue_code(job_db, workspace_id, "job-f-running", "heavy", order=0)
    round1 = claim_batch_with_retry(
        pool,
        "worker-1158-f",
        None,
        None,
        limit=1,
        code_limit=1,
        declared_node_limits={"heavy": 1},
    )
    assert [claim.job_id for claim in round1.claims] == ["job-f-running"]

    # 队列（按入队序）：两个 heavy 在前、light 在后——批预算 2 恰好会被
    # 两个已满节点候选烧光（修复前的空批形态：单 heavy + limit≥2 时第二个
    # 槽位仍会捞到 light，不构成饿死，故要两个 heavy 占满预算）。
    blocked_a = _enqueue_code(job_db, workspace_id, "job-f-heavy-a", "heavy", order=1)
    blocked_b = _enqueue_code(job_db, workspace_id, "job-f-heavy-b", "heavy", order=2)
    served = _enqueue_code(job_db, workspace_id, "job-f-light", "light", order=3)

    round2 = claim_batch_with_retry(
        pool,
        "worker-1158-f",
        None,
        None,
        limit=2,
        code_limit=2,
        declared_node_limits={"heavy": 1},
    )

    # 修复前：读相两个槽位都选中 heavy、写相双双拒收、返回空批；修复后：
    # 读相预过滤逐一跳过两个 heavy（各计一次），一批领走 light。
    assert [claim.job_id for claim in round2.claims] == ["job-f-light"]
    assert round2.skip_reasons.get("worker_node_limit_full") == 2
    assert _request_state(job_db, blocked_a) == "queued"  # 留队列等额度
    assert _request_state(job_db, blocked_b) == "queued"
    assert _request_state(job_db, served) == "claimed"


def test_write_phase_gate_rejects_second_candidate_after_declaration_sync(job_db) -> None:
    """用例 7：写相权威门（worker_node_admits）直调覆盖。读相预过滤上线后
    常规路径的 skip 都产自读相，写相拒绝路径的覆盖只剩「读相未声明、写相
    才同步到声明」的形态（滚动升级窗口 / 声明与选择之间的真实竞态）：先不
    声明上限调 select_batch_candidates（两个同节点候选都入选），再带
    declared_node_limits={key: 1} 直调 claim_batch_in_transaction——写相
    prepare_claim_view 在 agent_workers 行锁内同步声明后逐候选评估：第一个
    promote、第二个被写相门拒（skip 计数 + 留队列）。直调模式比照
    tests/db/test_claim_node_limit_remote.py 的 P2-2 用例。"""
    workspace_id, node_key = "ws-1158-g", "heavy"
    _seed_code_job(job_db, workspace_id, "job-g1", node_key)
    _seed_code_job(job_db, workspace_id, "job-g2", node_key)
    first = _enqueue_code(job_db, workspace_id, "job-g1", node_key, order=0)
    second = _enqueue_code(job_db, workspace_id, "job-g2", node_key, order=1)
    _register_worker("worker-1158-g")
    pool = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)

    # 读相未声明上限（库存 {}）：预过滤不启用，两个同节点候选都入选。
    selection = select_batch_candidates(pool, "worker-1158-g", None, None, limit=2)
    assert len(selection.candidates) == 2

    with write_transaction(TEST_DATABASE_URL) as conn:
        outcome = claim_batch_in_transaction(
            pool,
            conn,
            "worker-1158-g",
            None,
            None,
            selection=selection,
            declared_node_limits={node_key: 1},
        )

    assert [claim.job_id for claim in outcome.claims] == ["job-g1"]
    assert outcome.skip_reasons.get("worker_node_limit_full") == 1
    assert _request_state(job_db, first) == "claimed"
    assert _request_state(job_db, second) == "queued"  # 写相门拒收，留队列
    # 声明同步发生在写相：库存列随本批更新。
    assert _stored_node_limits(job_db, "worker-1158-g") == {node_key: 1}
