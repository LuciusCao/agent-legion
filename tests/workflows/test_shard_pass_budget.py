"""Shard fan-out pass budget (#401 review P1-1).

The v79 shard-aware index removed the accidental one-in-flight backpressure
on shard fan-outs; within ONE poll pass the code stock gate cannot see the
shards just submitted to the asynchronous enqueue pool (it counts only
committed queued rows). ``claim_shard_node`` therefore caps its own
submissions at ``CodeStockGate.pass_budget()`` — the remaining budget on the
gate's same target/clamps/TTL basis — and the next pass continues. These
tests pin: the pass cap holds under a large fan-out, the next pass drains
the rest, the disabled gate imposes no cap, and ``pass_budget`` mirrors
``allows`` on the boundary.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from server.app.configuration.executor_knobs import CodeStockConfig
from server.app.workflow_worker.code_stock import CodeStockGate
from server.app.workflows.definition import WorkflowNode
from server.app.workflows.schema import WorkflowShardSpec
from tests.postgres_support import TEST_DATABASE_URL


def _gate(config: CodeStockConfig | None = None) -> CodeStockGate:
    # A fresh gate per assertion (the TTL cache is exercised in production;
    # these tests pin the budget math and the pass cap, not the cache).
    return CodeStockGate(TEST_DATABASE_URL, config or CodeStockConfig())


def test_pass_budget_mirrors_allows_on_the_boundary(job_db) -> None:
    """pass_budget = target - queued: zero exactly where allows() flips."""
    gate = _gate(CodeStockConfig(min_stock=4, max_stock=4))
    # Prime the TTL snapshot first (a fresh gate's first allows() refreshes);
    # the hand-set _queued below must survive the subsequent TTL window.
    gate.allows()
    assert gate._target == 4
    gate._queued = 4
    assert gate.allows() is False and gate.pass_budget() == 0
    gate._queued = 2
    assert gate.allows() is True and gate.pass_budget() == 2
    gate._queued = 9  # over target: clamp at zero, never negative
    assert gate.pass_budget() == 0


def test_disabled_gate_has_no_pass_budget() -> None:
    assert _gate(CodeStockConfig(enabled=False)).pass_budget() is None


def _make_shard_node(count: int) -> WorkflowNode:
    return WorkflowNode(
        key="fan",
        label="fan",
        capability="fan",
        outputs=["out.json"],
        shard=WorkflowShardSpec(count=count),
    )


def _run_pass(worker, monkeypatch, *, succeed_remote: bool = True) -> int:
    """Run claim_shard_node once; return how many shards the pass submitted.

    Patches try_claim_code_worker_node (the remote lane) so each pending
    shard records a submission; the local lane is disabled by a zero
    code_capacity, making the remote counter the pass's submission count.
    """
    submitted = {"n": 0}

    def fake_remote_claim(
        worker_thread,
        workspace,
        job,
        node,
        job_dir,
        log_path,
        inputs,
        workflow_key,
        shard_runtime=None,
    ):
        if succeed_remote:
            submitted["n"] += 1
            return True
        return False

    monkeypatch.setattr(
        "server.app.workflow_worker.shards.try_claim_code_worker_node", fake_remote_claim
    )
    from server.app.executors.scheduling.capacity import CapacitySnapshot
    from server.app.workflow_worker.shards import claim_shard_node

    claim_shard_node(
        worker,
        {"id": "ws-budget"},
        {"id": "job-budget", "workspace_id": "ws-budget"},
        _make_shard_node(50),
        Path("/tmp/job-budget"),
        None,
        None,
        CapacitySnapshot(),
    )
    return submitted["n"]


def _budget_worker(job_db, tmp_path, monkeypatch, budget):
    """A minimal WorkflowWorkerThread-shaped object for claim_shard_node.

    The fan-out loop reads worker.job_db (shard rows), worker.settings
    (code_capacity for the local-lane gate) and worker.code_stock (the
    budget under test); a MagicMock carries all three without the full
    thread's scheduler surface.
    """
    worker = MagicMock()
    worker.job_db = job_db
    worker.settings.executor_runtime.code_capacity = 0  # pure-remote shape
    worker.settings.logs_dir = tmp_path
    worker.code_stock = _gate(CodeStockConfig(min_stock=budget, max_stock=budget))
    monkeypatch.setattr(
        "server.app.workflow_worker.code_stock.CodeStockGate._refresh", lambda self: None
    )
    worker.code_stock._target = budget
    worker.code_stock._queued = 0
    return worker


def test_single_pass_caps_submissions_at_budget(job_db, tmp_path, monkeypatch) -> None:
    """50 pending shards, budget 5: ONE pass submits exactly 5."""
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-budget', 'ws', 'demo_workflow') on conflict do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, status, storage_dir)"
            " values ('job-budget', 'ws-budget', 'question', 'job-budget', 'running', 'd')"
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values ('job-budget', 'fan', 'pending')"
        )
        conn.executemany(
            "insert into node_shards(job_id, node_key, shard_index, status, input_json)"
            " values ('job-budget', 'fan', %s, 'pending', '{}')",
            [(index,) for index in range(50)],
        )
    worker = _budget_worker(job_db, tmp_path, monkeypatch, budget=5)

    submitted = _run_pass(worker, monkeypatch)

    assert submitted == 5, "the pass must stop at the stock gate's remaining budget"


def test_next_pass_continues_draining(job_db, tmp_path, monkeypatch) -> None:
    """Budget 5, 8 shards: pass one submits 5, pass two drains the rest."""
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-budget', 'ws', 'demo_workflow') on conflict do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, status, storage_dir)"
            " values ('job-budget', 'ws-budget', 'question', 'job-budget', 'running', 'd')"
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values ('job-budget', 'fan', 'running')"
        )
        conn.executemany(
            "insert into node_shards(job_id, node_key, shard_index, status, input_json)"
            " values ('job-budget', 'fan', %s, 'pending', '{}')",
            [(index,) for index in range(8)],
        )
    worker = _budget_worker(job_db, tmp_path, monkeypatch, budget=5)

    first = _run_pass(worker, monkeypatch)
    assert first == 5
    # The five submitted shards are now running (the fake remote claim
    # bound them); mirror that so pass two sees only the pending three.
    with job_db.connect() as conn:
        conn.execute(
            "update node_shards set status='running'"
            " where job_id='job-budget' and node_key='fan' and shard_index < 5"
        )
    second = _run_pass(worker, monkeypatch)
    assert second == 3, "the next pass continues where the capped one stopped"


def test_disabled_gate_submits_whole_fanout(job_db, tmp_path, monkeypatch) -> None:
    """Gate off: no pass cap — the whole pending fan-out goes in one pass."""
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('ws-budget', 'ws', 'demo_workflow') on conflict do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, status, storage_dir)"
            " values ('job-budget', 'ws-budget', 'question', 'job-budget', 'running', 'd')"
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values ('job-budget', 'fan', 'running')"
        )
        conn.executemany(
            "insert into node_shards(job_id, node_key, shard_index, status, input_json)"
            " values ('job-budget', 'fan', %s, 'pending', '{}')",
            [(index,) for index in range(12)],
        )
    worker = _budget_worker(job_db, tmp_path, monkeypatch, budget=1)
    worker.code_stock = _gate(CodeStockConfig(enabled=False))

    submitted = _run_pass(worker, monkeypatch)

    assert submitted == 12, "disabled gate imposes no pass cap"


def test_local_shard_context_expected_outputs_exclude_ordinary_outputs(job_db) -> None:
    """#401 review P1-2 (local lane): claim_shard_locally builds the shard's
    ExecutionContext with exactly [shard_output-N.json] as its expected
    outputs — the mirror of the remote manifest contract, so sibling local
    shards sharing the job dir also promote disjoint names only."""
    from unittest.mock import patch

    from server.app.workflow_worker.shard_dispatch import claim_shard_locally

    node = _make_shard_node(4)
    contexts = []

    def fake_try_claim(_request):
        claim = MagicMock()
        claim.execution_id = "exec-local"
        claim.lease_id = "lease-local"
        claim.node_run_id = 1
        claim.executor_id = "code-default"
        claim.workspace_id = "ws-budget"
        claim.job_id = "job-budget"
        claim.workflow_key = "ws-budget"
        claim.node_key = "fan"
        return claim

    def fake_submit(_worker, _executor_id, _claim, context):
        contexts.append(context)

    worker = MagicMock()
    worker.leases.try_claim = fake_try_claim
    snapshot = MagicMock()
    snapshot.has_capacity.return_value = True
    with patch("server.app.workflow_worker.shard_dispatch.submit_claim", fake_submit):
        claimed = claim_shard_locally(
            worker,
            {"id": "ws-budget"},
            {"id": "job-budget", "workspace_id": "ws-budget"},
            node,
            Path("/tmp/job-budget"),
            Path("/tmp/shard.log"),
            shard_index=2,
            shard_input={"q": 2},
            local_node_limit=None,
            control_snapshot=None,
            allowed_node_keys=None,
            snapshot=snapshot,
        )

    assert claimed is True
    assert len(contexts) == 1
    context = contexts[0]
    # The node declares outputs=["out.json"]; the shard contract is only the
    # per-index file (P1-2 mirrors the remote manifest on the local lane).
    assert context.expected_outputs == ("shard_output-2.json",)
    assert "out.json" not in context.expected_outputs
    # The shard payload still reaches the child runtime dict.
    assert context.runtime["shard_index"] == 2
    assert context.runtime["shard_input"] == {"q": 2}
