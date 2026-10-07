"""Broker claim of a quality replay with a pinned node profile (#1079, #440 D6).

The replay copy job keeps its original ``workflow_revision_id`` (lineage,
stock tiers) while its snapshot carries the chosen profile. The claim's live
manifest must therefore NOT re-read execution / prompt from that revision for
a pinned request: the Worker receives the chosen model and prompt, and a
runtime switch is not judged unclaimable against the original revision.
Unpinned requests keep the live revision semantics (control case).
"""

from __future__ import annotations

import json

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_broker.unclaimable import fail_unclaimable_model_requests
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.services.node_profile_pins import MANIFEST_KEY
from tests.postgres_support import TEST_DATABASE_URL

_WS = "replay-ws"
_REVISION = "replay-ws:v1"


def _broker(job_db) -> AgentExecutionBroker:
    return AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)


def _seed(job_db, job_id: str, *, pinned: bool) -> str:
    """A job on the ORIGINAL revision (model-a / PROMPT-A, velites) whose
    queued request was enqueued with the chosen profile (model-b / PROMPT-B, pi)."""
    original = {
        "key": "t",
        "label": "t",
        "nodes": {
            "draft": {
                "key": "draft",
                "label": "draft",
                "capability": "draft",
                "node_type": "agent",
                "execution": {
                    "runtime": "velites",
                    "provider": "gateway",
                    "model": "model-a",
                    "prompt": "PROMPT-A",
                },
            }
        },
    }
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'Replay') on conflict(id) do nothing",
            (_WS,),
        )
        conn.execute(
            "insert into workflow_revisions(id, workspace_id, version, status, definition_json,"
            " definition_hash, published_at) values (%s, %s, 1, 'active', %s, 'h-a',"
            " current_timestamp) on conflict(id) do nothing",
            (_REVISION, _WS, json.dumps(original)),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, workflow_revision_id,"
            " workflow_definition_hash) values (%s, %s, 'question', %s, %s, 'h-b')",
            (job_id, _WS, job_id, _REVISION),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, 'draft')", (job_id,))
    manifest = {
        "job_id": job_id,
        "node_key": "draft",
        "log_path": f"logs/{job_id}.log",
        "runtime": "pi",
        "capability": "draft",
        "execution": {"provider": "gateway", "model": "model-b"},
        "additional_prompt": "PROMPT-B",
        "prompt_mode": "",
    }
    if pinned:
        manifest[MANIFEST_KEY] = {
            "revision_id": "replay-ws:v2",
            "node_key": "draft",
            "profile_hash": "ph",
        }
    execution_id = _broker(job_db).enqueue(
        AgentExecutionRequest(
            workspace_id=_WS,
            job_id=job_id,
            workflow_key=_WS,
            node_key="draft",
            agent_id="draft",
            agent_definition_hash="profile-hash",
            manifest=manifest,
            profile_source="node",
            runtime="pi",
            requires_labels={},
        )
    )
    assert execution_id is not None
    return execution_id


def _register(worker_id: str, model: str) -> None:
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id=worker_id,
        name=worker_id,
        runtimes=["pi"],
        models=[{"provider": "gateway", "model": model}],
        max_concurrency=10,
        labels={},
    )


def _state(job_db, execution_id: str) -> str:
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id=%s", (execution_id,)
        ).fetchone()
    return str(row["state"])


def test_pinned_replay_claims_with_the_chosen_model_and_prompt(job_db) -> None:
    execution_id = _seed(job_db, "replay-copy", pinned=True)
    _register("w-b", "model-b")

    claim = _broker(job_db).claim("w-b")

    assert claim is not None and claim.execution_id == execution_id
    assert claim.manifest["execution"]["model"] == "model-b"
    assert claim.manifest["additional_prompt"] == "PROMPT-B"
    assert claim.manifest[MANIFEST_KEY]["revision_id"] == "replay-ws:v2"


def test_unpinned_request_keeps_live_revision_execution(job_db) -> None:
    """Control: a normal job still re-reads its revision at claim (model-a)."""
    _seed(job_db, "normal-job", pinned=False)
    _register("w-b", "model-b")

    assert _broker(job_db).claim("w-b") is None  # live manifest says model-a
    _register("w-a", "model-a")
    claim = _broker(job_db).claim("w-a")
    assert claim is not None
    assert claim.manifest["additional_prompt"] == "PROMPT-A"


def test_pinned_runtime_switch_is_not_judged_unclaimable(job_db) -> None:
    execution_id = _seed(job_db, "replay-pi", pinned=True)
    _register("w-b", "model-b")

    failed = fail_unclaimable_model_requests(_broker(job_db))

    assert execution_id not in failed
    assert _state(job_db, execution_id) == "queued"
