"""Broker dual-source matrix (#933, #440 P2, schema v92).

Self-contained agent rows (``profile_source='node'``) carry runtime and
requires_labels on the request row and never touch ``versioned_entities``
or ``workspace_node_routes``; legacy rows keep the Agent-definition path.
Matrix: enqueue / claim / unclaimable sweep / stale-definition sweep /
stock buckets, for both sources side by side.
"""

from __future__ import annotations

import json

import pytest

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_broker.unclaimable import fail_unclaimable_model_requests
from server.app.agent_catalog import AgentDefinition
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.workflow_worker.agent_stock import AgentStockConfig
from server.app.workflow_worker.agent_stock_snapshot import load_stock_snapshot
from tests.helpers import replace_agent_catalog
from tests.helpers.agent_worker_api import seed_request
from tests.postgres_support import TEST_DATABASE_URL

_WS = "test-workspace"
_MANIFEST_EXECUTION = {"provider": "gateway", "model": "test-model"}


def _broker(job_db) -> AgentExecutionBroker:
    return AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)


def _seed_node_request(
    job_db,
    *,
    job_id: str,
    node_key: str = "draft",
    runtime: str = "velites",
    labels: dict[str, str] | None = None,
) -> str:
    """A self-contained agent request: no Agent definition, no route row."""
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'Test') on conflict(id) do nothing",
            (_WS,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values (%s, %s, 'question', %s)",
            (job_id, _WS, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, %s)", (job_id, node_key))
    execution_id = _broker(job_db).enqueue(
        AgentExecutionRequest(
            workspace_id=_WS,
            job_id=job_id,
            workflow_key=_WS,
            node_key=node_key,
            agent_id=node_key,
            agent_definition_hash="profile-hash",
            manifest={
                "job_id": job_id,
                "log_path": f"logs/{job_id}.log",
                "runtime": runtime,
                "capability": "draft",
                "execution": _MANIFEST_EXECUTION,
            },
            profile_source="node",
            runtime=runtime,
            requires_labels=labels if labels is not None else {"arch": "arm64"},
        )
    )
    assert execution_id is not None
    return execution_id


def _register(worker_id: str, *, runtimes: list[str], labels: dict[str, str]) -> None:
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id=worker_id,
        name=worker_id,
        runtimes=runtimes,
        models=[{"provider": "gateway", "model": "test-model"}],
        max_concurrency=10,
        labels=labels,
    )


def _row(job_db, execution_id: str) -> dict:
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select * from agent_execution_requests where execution_id=%s", (execution_id,)
        ).fetchone()
    return dict(row)


def test_node_rows_enqueue_without_route_or_definition(job_db) -> None:
    execution_id = _seed_node_request(job_db, job_id="node-job")

    row = _row(job_db, execution_id)
    assert row["profile_source"] == "node"
    assert row["runtime"] == "velites"
    assert json.loads(row["requires_labels_json"]) == {"arch": "arm64"}
    assert row["agent_id"] == "draft"
    assert row["agent_definition_hash"] == "profile-hash"
    assert row["node_concurrency_limit"] == 1  # no workspace cap → audit 1


def test_legacy_rows_keep_the_default_source_and_null_profile_columns(job_db) -> None:
    seed_request(job_db, job_id="legacy-job")
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select profile_source, runtime, requires_labels_json"
            " from agent_execution_requests where job_id='legacy-job'"
        ).fetchone()
    assert dict(row) == {
        "profile_source": "agent_definition",
        "runtime": None,
        "requires_labels_json": None,
    }


def test_legacy_rows_without_route_still_need_the_published_definition(job_db) -> None:
    """A route-less legacy request (job frozen before the node became
    self-contained, PR #1039 codex R3) is accepted only while its Agent
    definition hash is still the published one."""
    replace_agent_catalog(_WS, {})
    with job_db.connect() as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values ('no-route', %s, 'question', 'no-route')",
            (_WS,),
        )
    with pytest.raises(ValueError, match="Agent definition is unavailable"):
        _broker(job_db).enqueue(
            AgentExecutionRequest(
                workspace_id=_WS,
                job_id="no-route",
                workflow_key=_WS,
                node_key="draft",
                agent_id="draft",
                agent_definition_hash="h",
                manifest={"job_id": "no-route", "execution": _MANIFEST_EXECUTION},
            )
        )


def test_legacy_rows_without_route_enqueue_against_the_published_definition(job_db) -> None:
    definition = AgentDefinition(capability="draft", runtime="pi", skill="g/s")
    replace_agent_catalog(_WS, {"drafter": definition})
    with job_db.connect() as conn:
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id)"
            " values ('routeless', %s, 'question', 'routeless')",
            (_WS,),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values ('routeless', 'draft')")
    execution_id = _broker(job_db).enqueue(
        AgentExecutionRequest(
            workspace_id=_WS,
            job_id="routeless",
            workflow_key=_WS,
            node_key="draft",
            agent_id="drafter",
            agent_definition_hash=definition.definition_hash(),
            manifest={"job_id": "routeless", "execution": _MANIFEST_EXECUTION},
        )
    )
    assert execution_id is not None
    assert _row(job_db, execution_id)["profile_source"] == "agent_definition"


def test_node_rows_claim_from_row_runtime_and_labels(job_db) -> None:
    execution_id = _seed_node_request(job_db, job_id="claim-node")
    _register("w-pi", runtimes=["pi"], labels={"arch": "arm64"})
    _register("w-x86", runtimes=["velites"], labels={"arch": "x86_64"})
    _register("w-ok", runtimes=["velites"], labels={"arch": "arm64"})
    broker = _broker(job_db)

    assert broker.claim("w-pi") is None  # runtime mismatch
    assert broker.claim("w-x86") is None  # labels mismatch
    claim = broker.claim("w-ok")

    assert claim is not None
    assert claim.execution_id == execution_id
    assert claim.runtime == "velites"
    assert claim.agent_id == "draft"
    assert _row(job_db, execution_id)["state"] == "claimed"


def test_both_sources_claim_side_by_side(job_db) -> None:
    seed_request(job_db, job_id="legacy-side", runtime="velites")
    node_id = _seed_node_request(job_db, job_id="node-side")
    _register("w-both", runtimes=["velites"], labels={"arch": "arm64"})
    broker = _broker(job_db)

    claims = [broker.claim("w-both"), broker.claim("w-both")]

    assert {claim.job_id for claim in claims if claim is not None} == {"legacy-side", "node-side"}
    assert _row(job_db, node_id)["state"] == "claimed"


def test_stale_definition_sweep_skips_node_rows(job_db) -> None:
    """No Agent definition exists for a node row, by design: the stale sweep
    must leave it queued while still failing a genuinely stale legacy row."""
    seed_request(job_db, job_id="legacy-stale")
    node_id = _seed_node_request(job_db, job_id="node-fresh")
    replace_agent_catalog(_WS, {})  # every legacy definition is now gone

    failed = _broker(job_db).fail_stale_definition_requests()

    assert node_id not in failed
    assert len(failed) == 1
    assert _row(job_db, node_id)["state"] == "queued"
    assert job_db.get_job_node("legacy-stale", "generate")["status"] == "failed"


def test_unclaimable_sweep_judges_node_rows_by_their_row_runtime(job_db) -> None:
    mismatch = _seed_node_request(job_db, job_id="node-pi", node_key="a", runtime="pi")
    match = _seed_node_request(job_db, job_id="node-velites", node_key="b")
    _register("w-velites", runtimes=["velites"], labels={"arch": "arm64"})

    failed = fail_unclaimable_model_requests(_broker(job_db))

    assert failed == [mismatch]
    assert "runtime 'pi' not declared" in job_db.get_job_node("node-pi", "a")["error_message"]
    assert _row(job_db, match)["state"] == "queued"


def test_stock_buckets_node_rows_by_node_key(job_db) -> None:
    _seed_node_request(job_db, job_id="stock-1", node_key="draft")
    _seed_node_request(job_db, job_id="stock-2", node_key="draft")
    _seed_node_request(job_db, job_id="stock-3", node_key="review")
    seed_request(job_db, job_id="stock-legacy")

    snapshot = load_stock_snapshot(TEST_DATABASE_URL, AgentStockConfig())

    assert snapshot.buckets[(_WS, "draft")].queued == 2
    assert snapshot.buckets[(_WS, "review")].queued == 1
    assert snapshot.buckets[(_WS, "generator-v1")].queued == 1
