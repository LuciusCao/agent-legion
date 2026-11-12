from __future__ import annotations

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.scheduling.capacity import load_capacity_snapshot
from server.app.jobs import JobQueries
from tests.executors.leases.helpers import (
    _claim_request,
    _create_job_in_workspace,
    _setup_workspace,
)
from tests.postgres_support import TEST_DATABASE_URL


def test_workspace_a_can_starve_workspace_b_at_global_capacity(
    queries: JobQueries, repo_a: ExecutorLeaseRepository, repo_b: ExecutorLeaseRepository
) -> None:
    """EXEC-CAPACITY-001: the single code pool enforces one global capacity;
    workspace fairness is only the scheduler's round-robin (P-0.5)."""
    executor_id = "code"
    global_capacity = 2
    workspace_a, job_a1 = _setup_workspace(
        queries, "Workspace A", executor_id, workspace_limit=2, local_limit=None
    )
    job_a2 = _create_job_in_workspace(queries, workspace_a)
    workspace_b, job_b1 = _setup_workspace(
        queries, "Workspace B", executor_id, workspace_limit=2, local_limit=None
    )

    claim_a1 = repo_a.try_claim(
        _claim_request(
            workspace_a,
            job_a1,
            executor_id=executor_id,
            global_capacity=global_capacity,
            local_node_limit=None,
        )
    )
    claim_a2 = repo_a.try_claim(
        _claim_request(
            workspace_a,
            job_a2,
            executor_id=executor_id,
            global_capacity=global_capacity,
            local_node_limit=None,
        )
    )
    claim_b1 = repo_b.try_claim(
        _claim_request(
            workspace_b,
            job_b1,
            executor_id=executor_id,
            global_capacity=global_capacity,
            local_node_limit=None,
        )
    )

    assert claim_a1 is not None
    assert claim_a2 is not None
    assert claim_b1 is None


def test_local_node_limit_blocks_same_node_but_allows_other_local_node(
    queries: JobQueries, repo_a: ExecutorLeaseRepository, repo_b: ExecutorLeaseRepository
) -> None:
    executor_id = "code"
    workspace_id, job_id = _setup_workspace(
        queries,
        "ws-local",
        executor_id,
        workspace_limit=10,
        node_key="review_keywords",
        local_limit=1,
    )
    job = queries.get_job(job_id)
    assert job is not None
    other_node_key = "extract_entities"
    with queries.connect() as conn:
        conn.execute(
            "insert into workspace_node_limits(workspace_id, node_key, concurrency_limit) values (%s, %s, %s)",
            (workspace_id, other_node_key, 1),
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values (%s, %s, 'pending')"
            " on conflict (job_id, node_key) do nothing",
            (job_id, other_node_key),
        )

    claim_first = repo_a.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            node_key="review_keywords",
            executor_id=executor_id,
            global_capacity=10,
        )
    )
    claim_same_node = repo_b.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            node_key="review_keywords",
            executor_id=executor_id,
            global_capacity=10,
        )
    )
    claim_other_node = repo_b.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            node_key=other_node_key,
            executor_id=executor_id,
            global_capacity=10,
        )
    )

    assert claim_first is not None
    assert claim_same_node is None
    assert claim_other_node is not None


def test_claim_rejected_when_job_paused(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(queries, "ws-paused", "code-default", workspace_limit=2)
    queries.pause_job(job_id, "awaiting_resources")

    claim = repo_a.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            execution_mode="until_node",
            target_node_key="review_keywords",
            allowed_node_keys=("review_keywords",),
        )
    )
    assert claim is None

    with queries.connect() as conn:
        runs = conn.execute("select * from node_runs where job_id=%s", (job_id,)).fetchall()
        leases = conn.execute("select * from executor_leases where job_id=%s", (job_id,)).fetchall()
    assert len(runs) == 0
    assert len(leases) == 0


def test_claim_rejected_when_target_snapshot_stale(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries,
        "ws-stale",
        "code-default",
        workspace_limit=2,
        node_keys=["review_keywords", "clean_items"],
    )
    queries.set_job_execution_target(job_id, "review_keywords")

    # Snapshot computed before the user changed the target.
    stale_request = _claim_request(
        workspace_id,
        job_id,
        execution_mode="until_node",
        target_node_key="review_keywords",
        allowed_node_keys=("review_keywords",),
    )

    queries.set_job_execution_target(job_id, "clean_items")

    claim = repo_a.try_claim(stale_request)
    assert claim is None

    with queries.connect() as conn:
        runs = conn.execute("select * from node_runs where job_id=%s", (job_id,)).fetchall()
        leases = conn.execute("select * from executor_leases where job_id=%s", (job_id,)).fetchall()
    assert len(runs) == 0
    assert len(leases) == 0


def test_claim_with_stale_full_snapshot_is_rejected_when_job_is_run_to(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-full-ignore", "code-default", workspace_limit=2
    )
    queries.set_job_execution_target(job_id, "review_keywords")

    # The worker read full mode before the user switched the job to run-to.
    claim = repo_a.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            execution_mode="full",
            target_node_key="other",
            allowed_node_keys=("review_keywords",),
        )
    )
    assert claim is None

    with queries.connect() as conn:
        runs = conn.execute("select * from node_runs where job_id=%s", (job_id,)).fetchall()
        leases = conn.execute("select * from executor_leases where job_id=%s", (job_id,)).fetchall()
    assert len(runs) == 0
    assert len(leases) == 0


def test_claims_write_the_code_pool_executor_id(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    """EXEC-CODE-POOL-001: every claim joins the implicit code pool; lease
    rows carry the constant 'code' executor id (historical ids stay)."""
    workspace_id, job_id = _setup_workspace(queries, "ws-pool-id", "code", workspace_limit=2)

    claim = repo_a.try_claim(_claim_request(workspace_id, job_id))

    assert claim is not None
    assert claim.executor_id == "code"


def _set_lease_executor_id(queries: JobQueries, job_id: str, executor_id: str) -> None:
    """Morph a real claim's lease into another executor_id form (every other
    column stays the genuine claim-time row)."""
    with queries.connect() as conn:
        updated = conn.execute(
            "update executor_leases set executor_id=%s where job_id=%s",
            (executor_id, job_id),
        )
    assert updated.rowcount == 1


def test_local_node_limit_counts_only_code_lease_forms(
    queries: JobQueries, repo_a: ExecutorLeaseRepository, repo_b: ExecutorLeaseRepository
) -> None:
    """#1171：本地路径节点计数只并 code 形态租约——#1167 口径表（
    tests/db/test_claim_node_limit_remote.py 用例 10 的矩阵钉子）的本地镜像。
    三形态 × limit=1，各占独立 workspace 互不污染，占位与探测用不同 job
    （探测 claim 的拒绝必须来自限额计数，而不是节点已 running）：

    - ``code``（本地池）占位 → 本地 code claim 拒；
    - ``agent:code:%``（远程 code）占位 → 本地 code claim 拒；
    - ``agent:<id>``（agent 车道）占位 → 本地 code claim 放行。
    """
    cases = [
        # (占位租约形态, 探测 claim 是否应放行)
        (None, False),  # None = 保持真实 claim 的 'code' 形态
        ("agent:code:worker-remote-1", False),
        ("agent:legacy-agent", True),
    ]
    for index, (occupy_form, should_admit) in enumerate(cases):
        workspace_id, job_occ = _setup_workspace(
            queries, f"ws-1171-{index}", "code", node_key="pkg", local_limit=1
        )
        occupying = repo_a.try_claim(
            _claim_request(workspace_id, job_occ, node_key="pkg", global_capacity=10)
        )
        assert occupying is not None
        if occupy_form is not None:
            _set_lease_executor_id(queries, job_occ, occupy_form)
        with queries.connect() as conn:
            row = conn.execute(
                "select executor_id from executor_leases where job_id=%s", (job_occ,)
            ).fetchone()
        assert row is not None
        expected_form = occupy_form if occupy_form is not None else "code"
        assert row["executor_id"] == expected_form  # 行形态入断言（口径表维度）

        job_probe = _create_job_in_workspace(queries, workspace_id, node_key="pkg")
        probe = repo_b.try_claim(
            _claim_request(workspace_id, job_probe, node_key="pkg", global_capacity=10)
        )
        if should_admit:
            assert probe is not None
        else:
            assert probe is None


def test_capacity_snapshot_counts_only_code_lease_forms(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    """#1171：调度快照（hint）与两条 claim 路径同谓词——节点已用量只并
    code 形态租约。limit=2 下 agent:code 占 1 + agent:<id> 占 1 → 余量 1
    （修复前快照全计，余量 0，与修复后的 claim 判定自相矛盾）。"""
    workspace_id, job_occ = _setup_workspace(
        queries, "ws-1171-snapshot", "code", node_key="pkg", local_limit=2
    )
    claim = repo_a.try_claim(
        _claim_request(
            workspace_id, job_occ, node_key="pkg", local_node_limit=2, global_capacity=10
        )
    )
    assert claim is not None
    _set_lease_executor_id(queries, job_occ, "agent:code:worker-remote-1")
    job_agent = _create_job_in_workspace(queries, workspace_id, node_key="pkg")
    claim_agent = repo_a.try_claim(
        _claim_request(
            workspace_id, job_agent, node_key="pkg", local_node_limit=2, global_capacity=10
        )
    )
    assert claim_agent is not None
    _set_lease_executor_id(queries, job_agent, "agent:legacy-agent")

    snapshot = load_capacity_snapshot(TEST_DATABASE_URL, code_capacity=16)

    assert snapshot.node_remaining[(workspace_id, "pkg")] == 1
