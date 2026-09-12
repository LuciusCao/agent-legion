"""#591 result-commit group-commit batching tests.

Three layers:

- queue unit tests live in the sister ``test_result_commit_batcher_queue.py``;
  this file owns repository and wired integration behavior;
- repository layer (real PostgreSQL): ``finish_many`` / ``mark_done_many``
  per-item semantics against seeded leases/requests — the 409 verdicts are
  data, not errors, and one item's rejection must not fail its neighbours;
- integration layer: the wired plane path (batcher + broker + leases) from
  ``submit`` through the writer thread to committed terminal state,
  including the ordering contract (finish before mark_done) and the
  kill-switch (batcher None → direct paths);
- #609 review round: the batched arm must keep the direct path's
  completed/failed gate on events post-processing (a cancelled result
  parses no partial events.jsonl) and its #521 lease_write/events stage
  marks on the submitting thread (#530: cancelled/409 never report an
  events segment);
- #609 round 3 (P2): the mark_done arm binds the retry wrapper
  (cross-replica symmetry with the finish arm); the max-items split and
  the stop exit-drain are pinned for real (direct queue fill / writer
  parked inside an arm); the wired path runs the PRODUCTION order
  (finish → mark_done per request); a mixed-409 mark_done batch and a
  mixed-kind round (the finish transaction commits before the mark_done
  one) are covered.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import server.app.agent_broker.result_commit_batcher as _batcher_module
from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.mark_done_batch import mark_done_many_with_retry
from server.app.agent_broker.result_commit_batcher import ResultCommitBatcher
from server.app.agent_broker.result_timing import ResultStageTimer
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.executors import _lease_finish_batch
from server.app.executors._lease_finish_batch import finish_many_with_retry
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult
from tests.helpers.agent_worker_api import seed_request
from tests.postgres_support import TEST_DATABASE_URL


def _setup_worker(job_db, worker_id: str = "worker-1") -> None:
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    registry.issue_token(
        worker_id=worker_id,
        name=worker_id,
        runtimes=["pi"],
        max_concurrency=50,
        labels={"arch": "arm64"},
    )


def _claim_one(job_db, worker_id: str = "worker-1"):
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    return broker.claim(worker_id)


def _seed_run_events(job_db, job_id: str, run_token: str) -> str:
    """Write a two-line events.jsonl (one usage event + one droppable delta)
    and a run.json under the job's run dir; return the data-dir-relative
    run_dir for the ExecutionResult."""
    run_dir = job_db.jobs_dir / "test-workspace" / job_id / "runs" / "generate" / run_token
    run_dir.mkdir(parents=True, exist_ok=True)
    events = [
        {"type": "message_end", "message": {"usage": {"input": 20, "output": 10, "cacheRead": 2}}},
        {"type": "text_delta", "message": {"text": "streaming"}},
    ]
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )
    (run_dir / "run.json").write_text(
        json.dumps(
            {"model": {"provider": "gateway", "model": "test-model"}, "skill_version": "v1"}
        ),
        encoding="utf-8",
    )
    return str(run_dir.relative_to(job_db.jobs_dir.parent))


def _usage_node_run_ids(job_db) -> set[int]:
    with job_db.connect() as conn:
        rows = conn.execute("select node_run_id from node_run_token_usage").fetchall()
    return {int(row["node_run_id"]) for row in rows}


# -------------------------------------------------------- repository layer


def test_finish_many_matches_direct_semantics(job_db) -> None:
    """Two finished leases + one inactive: verdicts and terminal state match
    the direct path, one transaction for the slice."""
    seed_request(job_db, job_id="job-1", limit=10)
    seed_request(job_db, job_id="job-2", limit=10)
    _setup_worker(job_db)
    leases = ExecutorLeaseRepository(job_db, data_dir=job_db.jobs_dir.parent)
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    first = broker.claim("worker-1")
    second = broker.claim("worker-1")

    result = ExecutionResult(status="completed", exit_code=0)
    verdicts, callbacks = finish_many_with_retry(
        leases, [(first.lease_id, result, None), (second.lease_id, result, None)]
    )

    assert verdicts == [True, True]
    # C5: post-commit work (events + broadcast) comes back as per-item
    # callbacks for the SUBMITTING thread, not run here.
    assert len(callbacks) == 2 and all(callable(c) for c in callbacks)
    assert job_db.get_job_node("job-1", "generate")["status"] == "completed"
    assert job_db.get_job_node("job-2", "generate")["status"] == "completed"
    # Inactive lease: False verdict, not an exception (409 is data).
    verdicts, callbacks = finish_many_with_retry(leases, [(first.lease_id, result, None)])
    assert verdicts == [False]
    assert callbacks == [None]


def test_mark_done_many_matches_direct_semantics(job_db) -> None:
    seed_request(job_db, job_id="job-1", limit=10)
    _setup_worker(job_db)
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    claim = broker.claim("worker-1")
    outcome = {"status": "completed"}

    verdicts = broker.mark_done_many([(claim.execution_id, "worker-1", claim.lease_id, outcome)])
    assert verdicts == [claim.lease_id]

    # A repeat (request already done) returns None — the same verdict the
    # direct path gives a late second attempt.
    assert broker.mark_done_many([(claim.execution_id, "worker-1", claim.lease_id, outcome)]) == [
        None
    ]


def test_mark_done_many_mixed_409_in_batch_is_data(job_db) -> None:
    """#609 P2-B: [success, None, success] in ONE shared transaction — a
    mismatched guard (wrong lease/worker/already-done) is per-item data
    that must neither fail the neighbours nor abort the transaction."""
    for index in range(3):
        seed_request(job_db, job_id=f"job-{index}", limit=10)
    _setup_worker(job_db)
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    claims = [broker.claim("worker-1") for _ in range(3)]
    assert all(c is not None for c in claims)
    outcome = {"status": "completed"}

    verdicts = broker.mark_done_many(
        [
            (claims[0].execution_id, "worker-1", claims[0].lease_id, outcome),
            # Guard miss: a stale lease_id from a previous attempt shape —
            # the row exists but the triple does not match, so the guarded
            # SELECT ... FOR UPDATE returns no row for exactly this entry.
            (claims[1].execution_id, "worker-1", "lease-not-the-bound-one", outcome),
            (claims[2].execution_id, "worker-1", claims[2].lease_id, outcome),
        ]
    )
    assert verdicts == [claims[0].lease_id, None, claims[2].lease_id]
    with job_db.connect() as conn:
        rows = conn.execute(
            "select execution_id, state from agent_execution_requests order by execution_id"
        ).fetchall()
    states = {str(row["execution_id"]): str(row["state"]) for row in rows}
    assert states[claims[0].execution_id] == "done"
    assert states[claims[1].execution_id] == "claimed"
    assert states[claims[2].execution_id] == "done"


# -------------------------------------------------------- integration layer


def test_wired_batcher_commits_wave_end_to_end(job_db) -> None:
    """The full #591 path in PRODUCTION order (#609 P2-B): per request the
    commit path runs finish → mark_done (``commit_agent_result``'s shape),
    never the reverse. A wave of four rides the writer thread as one
    finish round + one mark_done round and lands committed terminal state;
    the request closes stay bound to their leases (a mark_done that ran
    first would strand the leases the finishes need)."""
    for index in range(4):
        seed_request(job_db, job_id=f"job-{index}", limit=10)
    _setup_worker(job_db)
    leases = ExecutorLeaseRepository(job_db, data_dir=job_db.jobs_dir.parent)
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    batcher = ResultCommitBatcher(
        partial(finish_many_with_retry, leases),
        partial(mark_done_many_with_retry, broker),
    )
    broker.result_batcher = batcher
    leases.result_batcher = batcher
    batcher.start()
    try:
        claims = [broker.claim("worker-1") for _ in range(4)]
        assert all(c is not None for c in claims)
        with ThreadPoolExecutor(max_workers=4) as pool:
            rounds = [
                f.result(timeout=10)
                for f in [
                    # finish FIRST, then the callback-shaped mark_done —
                    # the two-phase contract the sweeper's crash window
                    # relies on.
                    pool.submit(
                        _finish_then_mark_done,
                        leases,
                        broker,
                        claim,
                        ExecutionResult(status="completed", exit_code=0),
                    )
                    for claim in claims
                ]
            ]
        assert rounds == [(True, True)] * 4
    finally:
        batcher.stop()
    for index in range(4):
        assert job_db.get_job_node(f"job-{index}", "generate")["status"] == "completed"
    with job_db.connect() as conn:
        rows = conn.execute(
            "select state, lease_id from agent_execution_requests"
            " where job_id in ('job-0', 'job-1', 'job-2', 'job-3')"
        ).fetchall()
    assert rows and all(str(row["state"]) == "done" and row["lease_id"] is not None for row in rows)


def _finish_then_mark_done(leases, broker, claim, result) -> tuple[bool, bool]:
    """One commit thread's production sequence: finish() → mark_done()."""
    finished = leases.finish(claim.lease_id, result)
    done = (
        broker.mark_done(claim.execution_id, "worker-1", claim.lease_id, {"status": "completed"})
        is not None
    )
    return finished, done


def test_mixed_kind_round_commits_finish_before_mark_done(job_db, monkeypatch) -> None:
    """#609 P2-B: a mixed round (finish + mark_done drained together) runs
    the finish arm's transaction to completion BEFORE the mark_done arm's —
    the ordering contract that keeps every interleaving of one request's
    pair on the right side of the sweeper's crash window. Pinned by
    recording arm invocations on the shared writer thread."""
    seed_request(job_db, job_id="job-1", limit=10)
    seed_request(job_db, job_id="job-2", limit=10)
    _setup_worker(job_db)
    leases = ExecutorLeaseRepository(job_db, data_dir=job_db.jobs_dir.parent)
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    arm_order: list[tuple[str, int]] = []
    first_finish_entered = threading.Event()
    release_first_finish = threading.Event()
    finish_calls = 0

    def _finish_arm(writes):  # noqa: ANN001
        nonlocal finish_calls
        finish_calls += 1
        arm_order.append(("finish", len(writes)))
        if finish_calls == 1:
            first_finish_entered.set()
            release_first_finish.wait(timeout=5)
        return finish_many_with_retry(leases, writes)

    def _mark_done_arm(writes):  # noqa: ANN001
        arm_order.append(("mark_done", len(writes)))
        return mark_done_many_with_retry(broker, writes)

    batcher = ResultCommitBatcher(_finish_arm, _mark_done_arm)
    round_kinds: list[list[str]] = []
    real_run_batch = batcher._run_batch

    def _recording_run_batch(batch):  # noqa: ANN001
        round_kinds.append([item.kind for item in batch])
        return real_run_batch(batch)

    monkeypatch.setattr(batcher, "_run_batch", _recording_run_batch)
    broker.result_batcher = batcher
    leases.result_batcher = batcher
    batcher.start()
    try:
        claims = [broker.claim("worker-1") for _ in range(2)]
        assert all(c is not None for c in claims)
        result = ExecutionResult(status="completed", exit_code=0)
        # Hold the first round inside its finish arm. Both kinds then queue
        # behind it before release, forcing the next drain to be a mixed
        # round rather than relying on scheduler timing.
        with ThreadPoolExecutor(max_workers=3) as pool:
            first_finish = pool.submit(leases.finish, claims[0].lease_id, result)
            assert first_finish_entered.wait(timeout=5)
            rest = [
                pool.submit(leases.finish, claims[1].lease_id, result),
                pool.submit(
                    broker.mark_done,
                    claims[0].execution_id,
                    "worker-1",
                    claims[0].lease_id,
                    {"status": "completed"},
                ),
            ]
            deadline = time.monotonic() + 5
            while batcher._queue.qsize() < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert batcher._queue.qsize() >= 2, "mixed items never reached the queue"
            release_first_finish.set()
            assert first_finish.result(timeout=10) is True
            assert all(f.result(timeout=10) for f in rest)
    finally:
        release_first_finish.set()
        batcher.stop()
    assert round_kinds[0] == ["finish"]
    assert len(round_kinds) == 2 and sorted(round_kinds[1]) == ["finish", "mark_done"]
    assert [kind for kind, _ in arm_order] == ["finish", "finish", "mark_done"]
    for index in range(len(claims)):
        assert job_db.get_job_node(f"job-{index + 1}", "generate")["status"] == "completed"


def test_kill_switch_none_batcher_takes_direct_path(job_db) -> None:
    """batcher=None: finish/mark_done run the direct serial paths (the
    pre-#591 behavior every existing test already pins)."""
    seed_request(job_db, job_id="job-1", limit=10)
    _setup_worker(job_db)
    leases = ExecutorLeaseRepository(job_db, data_dir=job_db.jobs_dir.parent)
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    assert leases.result_batcher is None and broker.result_batcher is None

    claim = broker.claim("worker-1")
    assert leases.finish(claim.lease_id, ExecutionResult(status="completed", exit_code=0)) is True
    assert broker.mark_done(claim.execution_id, "worker-1", claim.lease_id, {}) is not None
    assert job_db.get_job_node("job-1", "generate")["status"] == "completed"


def test_finish_many_sorts_writes_by_job_for_counter_lock_order(job_db) -> None:
    """C4/#609 P1-2: the batch writes in (workspace_id, run_id, job_id)
    order regardless of queue order — the full counter-key sequence the
    status triggers read and try_claim_many shares. job_id alone does not
    pin it: two jobs sorted X→Y by job_id can live in workspaces ordered
    Y→X, reopening the cross-batch 40P01 ring."""
    # Two workspaces with INVERTED id vs job_id order: ws-a's job sorts
    # after ws-b's by job_id but before it by workspace_id.
    seed_request(job_db, job_id="job-01", limit=10, workspace_id="ws-a")
    seed_request(job_db, job_id="job-02", limit=10, workspace_id="ws-a")
    seed_request(job_db, job_id="job-03", limit=10, workspace_id="ws-b")
    seed_request(job_db, job_id="job-04", limit=10, workspace_id="ws-b")
    # Distinct run_ids with their own inversion inside ws-a: job-01's run
    # sorts after job-02's.
    with job_db.connect() as conn:
        conn.execute("update jobs set run_id='run-B' where id='job-01'")
        conn.execute("update jobs set run_id='run-A' where id='job-02'")
        conn.execute("update jobs set run_id='run-A' where id='job-03'")
        conn.execute("update jobs set run_id='run-B' where id='job-04'")
    _setup_worker(job_db)
    leases = ExecutorLeaseRepository(job_db, data_dir=job_db.jobs_dir.parent)
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    claimed = [broker.claim("worker-1") for _ in range(4)]
    assert all(c is not None for c in claimed)
    # Reverse queue order; the batch must still write the (ws, run, job)
    # order: (ws-a, run-A, job-02), (ws-a, run-B, job-01),
    # (ws-b, run-A, job-03), (ws-b, run-B, job-04).
    order: list[tuple[str, str, str]] = []
    real_finish_lease = _lease_finish_batch.finish_lease

    def _recording_finish_lease(conn, lease_id, result, data_dir=None):  # noqa: ANN001
        lease = conn.execute(
            "select j.workspace_id, j.run_id, l.job_id from executor_leases l"
            " left join jobs j on j.id = l.job_id where l.id = %s",
            (lease_id,),
        ).fetchone()
        order.append((str(lease["workspace_id"]), str(lease["run_id"]), str(lease["job_id"])))
        return real_finish_lease(conn, lease_id, result, data_dir)

    import server.app.executors._lease_finish_batch as batch_module

    batch_module.finish_lease = _recording_finish_lease
    try:
        writes = [
            (claim.lease_id, ExecutionResult(status="completed", exit_code=0), None)
            for claim in reversed(claimed)
        ]
        verdicts, _callbacks = _lease_finish_batch.finish_many_with_retry(leases, writes)
    finally:
        batch_module.finish_lease = real_finish_lease
    assert verdicts == [True] * 4
    assert order == sorted(order), "writes must run in (workspace, run, job) order"
    assert order == [
        ("ws-a", "run-A", "job-02"),
        ("ws-a", "run-B", "job-01"),
        ("ws-b", "run-A", "job-03"),
        ("ws-b", "run-B", "job-04"),
    ]


def test_post_stop_submit_takes_direct_path(job_db) -> None:
    """C6: after stop(), a racing submit runs the direct serial write
    instead of parking on the drained queue forever."""
    seed_request(job_db, job_id="job-1", limit=10)
    _setup_worker(job_db)
    leases = ExecutorLeaseRepository(job_db, data_dir=job_db.jobs_dir.parent)
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    batcher = ResultCommitBatcher(
        partial(finish_many_with_retry, leases), partial(mark_done_many_with_retry, broker)
    )
    broker.result_batcher = batcher
    leases.result_batcher = batcher
    claim = broker.claim("worker-1")

    batcher.start()
    batcher.stop()
    # The writer is gone; this submit must still complete via the direct
    # fallback, not hang.
    verdict = leases.finish(claim.lease_id, ExecutionResult(status="completed", exit_code=0))
    assert verdict is True
    assert job_db.get_job_node("job-1", "generate")["status"] == "completed"


def test_writer_offloads_post_commit_to_submitter(job_db) -> None:
    """C5: the events post-processing/broadcast callbacks run on the
    SUBMITTING thread (after submit returns), never on the writer."""
    seed_request(job_db, job_id="job-1", limit=10)
    _setup_worker(job_db)
    leases = ExecutorLeaseRepository(job_db, data_dir=job_db.jobs_dir.parent)
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    batcher = ResultCommitBatcher(
        partial(finish_many_with_retry, leases), partial(mark_done_many_with_retry, broker)
    )
    broker.result_batcher = batcher
    leases.result_batcher = batcher
    claim = broker.claim("worker-1")
    seen_threads: list[str] = []
    real_run = _batcher_module._run_post_commit

    def _recording_run(callback):  # noqa: ANN001
        seen_threads.append(threading.current_thread().name)
        real_run(callback)

    _batcher_module._run_post_commit = _recording_run
    batcher.start()
    try:
        assert (
            leases.finish(claim.lease_id, ExecutionResult(status="completed", exit_code=0)) is True
        )
    finally:
        _batcher_module._run_post_commit = real_run
        batcher.stop()
    assert seen_threads and all(name != "result-commit-batcher" for name in seen_threads), (
        "post-commit work must not run on the writer thread"
    )


# ------------------------------------------- #609 review round (P1-1 / P1-2)


def test_submit_racing_stop_never_enqueues_onto_dead_writer(job_db) -> None:
    """P1-1: the closed-check and the enqueue are one atomic pair against
    stop()'s close+sentinel. Orchestrated at the exact old race window: a
    submitter parks between its check and its enqueue (a parking _BatchItem
    constructor), stop() runs to completion (writer joined and gone), then
    the submitter is released — it must take the direct path, never enqueue
    onto the dead writer and park on a future nobody resolves."""
    seed_request(job_db, job_id="job-1", limit=10)
    _setup_worker(job_db)
    leases = ExecutorLeaseRepository(job_db, data_dir=job_db.jobs_dir.parent)
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    batcher = ResultCommitBatcher(
        partial(finish_many_with_retry, leases), partial(mark_done_many_with_retry, broker)
    )
    broker.result_batcher = batcher
    leases.result_batcher = batcher
    claim = broker.claim("worker-1")
    batcher.start()

    entered_ctor = threading.Event()
    release_ctor = threading.Event()
    real_item_cls = _batcher_module._BatchItem

    class _ParkingItem(real_item_cls):
        def __init__(self, kind, args):  # noqa: ANN001
            # Fires between submit()'s closed-check and its enqueue (the
            # old race window; the new code has not taken the lock yet).
            entered_ctor.set()
            release_ctor.wait(timeout=5)
            super().__init__(kind=kind, args=args)

    _batcher_module._BatchItem = _ParkingItem
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            verdict = pool.submit(
                leases.finish,
                claim.lease_id,
                ExecutionResult(status="completed", exit_code=0),
            )
            assert entered_ctor.wait(timeout=5), "submitter never reached the race window"
            # The submitter is parked past its closed-check: stop() closes
            # the gate, feeds the sentinel, and joins the (now dead) writer.
            batcher.stop()
            release_ctor.set()
            # Bounded wait: the unfixed race parks this forever — fail fast
            # instead of hanging the suite.
            assert verdict.result(timeout=5) is True
    finally:
        _batcher_module._BatchItem = real_item_cls
    assert job_db.get_job_node("job-1", "generate")["status"] == "completed"


def test_batched_cancelled_finish_skips_events_post_processing(job_db) -> None:
    """P1-1: the batched arm keeps the direct path's completed/failed gate
    on events post-processing — a cancelled result parses no partial
    events.jsonl, persists no token-usage row, and compresses nothing,
    while a completed neighbour in the SAME batch gets all of it."""
    seed_request(job_db, job_id="job-1", limit=10)
    seed_request(job_db, job_id="job-2", limit=10)
    _setup_worker(job_db)
    leases = ExecutorLeaseRepository(job_db, data_dir=job_db.jobs_dir.parent)
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    claims = [broker.claim("worker-1") for _ in range(2)]
    assert all(claim is not None for claim in claims)
    by_job = {claim.job_id: claim for claim in claims}

    cancelled_rel = _seed_run_events(job_db, "job-1", "cancel-token")
    completed_rel = _seed_run_events(job_db, "job-2", "done-token")
    data_dir = job_db.jobs_dir.parent

    writes = [
        (
            by_job["job-1"].lease_id,
            ExecutionResult(status="cancelled", exit_code=130, run_dir=cancelled_rel),
            None,
        ),
        (
            by_job["job-2"].lease_id,
            ExecutionResult(status="completed", exit_code=0, run_dir=completed_rel),
            None,
        ),
    ]
    verdicts, callbacks = finish_many_with_retry(leases, writes)
    assert verdicts == [True, True]
    for callback in callbacks:
        assert callback is not None
        callback()  # the submitting thread's post-commit work

    usage_ids = _usage_node_run_ids(job_db)
    # Cancelled: no token-usage row (the direct path never writes one).
    assert by_job["job-1"].node_run_id not in usage_ids
    # Completed: the usage event was parsed and persisted.
    assert by_job["job-2"].node_run_id in usage_ids
    # Cancelled: the partial events.jsonl is left intact for debugging.
    cancelled_text = (data_dir / cancelled_rel / "events.jsonl").read_text(encoding="utf-8")
    assert "text_delta" in cancelled_text and "message_end" in cancelled_text
    # Completed: PI compression dropped the delta, kept the usage event.
    completed_text = (data_dir / completed_rel / "events.jsonl").read_text(encoding="utf-8")
    assert "text_delta" not in completed_text and "message_end" in completed_text


def test_batched_finish_marks_result_stages_on_submitting_thread(job_db) -> None:
    """P1-2: the #521 lease_write/events segments survive batching — both
    marked on the SUBMITTING thread (lease_write honestly spans queue wait
    + the shared batch), events only for the completed/failed family: a
    cancelled or 409 item never reports an events segment (#530)."""
    seed_request(job_db, job_id="job-1", limit=10)
    seed_request(job_db, job_id="job-2", limit=10)
    _setup_worker(job_db)
    leases = ExecutorLeaseRepository(job_db, data_dir=job_db.jobs_dir.parent)
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    batcher = ResultCommitBatcher(
        partial(finish_many_with_retry, leases), partial(mark_done_many_with_retry, broker)
    )
    broker.result_batcher = batcher
    leases.result_batcher = batcher
    claims = [broker.claim("worker-1") for _ in range(2)]
    assert all(claim is not None for claim in claims)
    completed_rel = _seed_run_events(job_db, claims[0].job_id, "stage-token")

    marks: list[tuple[str, str]] = []
    real_mark = _lease_finish_batch._mark_result_stage

    def _recording_mark(timer, name):  # noqa: ANN001
        marks.append((name, threading.current_thread().name))
        real_mark(timer, name)

    _lease_finish_batch._mark_result_stage = _recording_mark
    batcher.start()
    try:
        completed_timer = ResultStageTimer()
        assert (
            leases.finish(
                claims[0].lease_id,
                ExecutionResult(status="completed", exit_code=0, run_dir=completed_rel),
                stage_timer=completed_timer,
            )
            is True
        )
        cancelled_timer = ResultStageTimer()
        assert (
            leases.finish(
                claims[1].lease_id,
                ExecutionResult(status="cancelled", exit_code=130),
                stage_timer=cancelled_timer,
            )
            is True
        )
        inactive_timer = ResultStageTimer()
        # The cancelled finish released the lease: this re-finish is a 409
        # verdict, which must still mark lease_write (never events).
        assert (
            leases.finish(
                claims[1].lease_id,
                ExecutionResult(status="completed", exit_code=0),
                stage_timer=inactive_timer,
            )
            is False
        )
    finally:
        _lease_finish_batch._mark_result_stage = real_mark
        batcher.stop()

    assert set(completed_timer.stages) == {"lease_write", "events"}
    assert set(cancelled_timer.stages) == {"lease_write"}
    assert set(inactive_timer.stages) == {"lease_write"}
    # Every mark closed on the submitting thread, never the single writer.
    assert marks and all(name != "result-commit-batcher" for _, name in marks)


# --------------------------------------------- #609 round-3 P2 (P2-A wiring)


def test_mark_done_arm_retries_deadlock(job_db, monkeypatch) -> None:
    """P2-A: the retry wrapper the plane binds — a retryable 40P01 from the
    shared mark_done transaction is retried (symmetric with
    ``finish_many_with_retry``), so the writer's whole round AND the
    isolation fallback's single-item replays (they ride the same bound
    arm) are covered under cross-replica contention."""
    import psycopg

    import server.app.agent_broker.mark_done_batch as mark_done_batch_module

    attempts = 0

    def _flaky(broker, writes):  # noqa: ANN001
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise psycopg.errors.DeadlockDetected("deadlock detected")
        return [None] * len(writes)

    monkeypatch.setattr(mark_done_batch_module, "mark_done_many", _flaky)
    verdicts = mark_done_many_with_retry(object(), [("e", "w", "l", {})])
    assert verdicts == [None]
    assert attempts == 2
