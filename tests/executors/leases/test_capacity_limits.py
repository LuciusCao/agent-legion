from __future__ import annotations

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from tests.executors.leases.helpers import (
    _claim_request,
    _create_job_in_workspace,
    _setup_workspace,
)


def test_workspace_a_can_starve_workspace_b_at_global_capacity(
    queries: JobQueries, repo_a: ExecutorLeaseRepository, repo_b: ExecutorLeaseRepository
) -> None:
    executor_id = "local-default"
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
    executor_id = "local-default"
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
            "insert into workspace_node_bindings(workspace_id, workflow_key, node_key, executor_id) values (?, ?, ?, ?)",
            (workspace_id, "reading_analysis", other_node_key, executor_id),
        )
        conn.execute(
            "insert into workspace_node_limits(workspace_id, workflow_key, node_key, concurrency_limit) values (?, ?, ?, ?)",
            (workspace_id, "reading_analysis", other_node_key, 1),
        )
        conn.execute(
            "insert or ignore into job_nodes(job_id, node_key, status) values (?, ?, 'pending')",
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


def test_agent_claim_with_no_local_node_limit_uses_global_and_workspace_only(
    queries: JobQueries, repo_a: ExecutorLeaseRepository, repo_b: ExecutorLeaseRepository
) -> None:
    executor_id = "agent-default"
    workspace_id, job_id_a = _setup_workspace(
        queries, "ws-agent", executor_id, workspace_limit=2, local_limit=None
    )
    job_id_b = _create_job_in_workspace(queries, workspace_id)

    claim_a = repo_a.try_claim(
        _claim_request(
            workspace_id,
            job_id_a,
            executor_id=executor_id,
            global_capacity=2,
            local_node_limit=None,
        )
    )
    claim_b = repo_b.try_claim(
        _claim_request(
            workspace_id,
            job_id_b,
            executor_id=executor_id,
            global_capacity=2,
            local_node_limit=None,
        )
    )
    claim_c = repo_a.try_claim(
        _claim_request(
            workspace_id,
            job_id_a,
            executor_id=executor_id,
            global_capacity=2,
            local_node_limit=None,
        )
    )

    assert claim_a is not None
    assert claim_b is not None
    assert claim_c is None


def test_claim_rejected_when_job_paused(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-paused", "local-default", workspace_limit=2
    )
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
        runs = conn.execute("select * from node_runs where job_id=?", (job_id,)).fetchall()
        leases = conn.execute("select * from executor_leases where job_id=?", (job_id,)).fetchall()
    assert len(runs) == 0
    assert len(leases) == 0


def test_claim_rejected_when_target_snapshot_stale(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries,
        "ws-stale",
        "local-default",
        workspace_limit=2,
        node_keys=["review_keywords", "clean_and_parse"],
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

    queries.set_job_execution_target(job_id, "clean_and_parse")

    claim = repo_a.try_claim(stale_request)
    assert claim is None

    with queries.connect() as conn:
        runs = conn.execute("select * from node_runs where job_id=?", (job_id,)).fetchall()
        leases = conn.execute("select * from executor_leases where job_id=?", (job_id,)).fetchall()
    assert len(runs) == 0
    assert len(leases) == 0


def test_claim_with_stale_full_snapshot_is_rejected_when_job_is_run_to(
    queries: JobQueries, repo_a: ExecutorLeaseRepository
) -> None:
    workspace_id, job_id = _setup_workspace(
        queries, "ws-full-ignore", "local-default", workspace_limit=2
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
        runs = conn.execute("select * from node_runs where job_id=?", (job_id,)).fetchall()
        leases = conn.execute("select * from executor_leases where job_id=?", (job_id,)).fetchall()
    assert len(runs) == 0
    assert len(leases) == 0
