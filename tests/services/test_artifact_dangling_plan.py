"""Scope, health gates, pagination, and stability of the dry-run plan (#1019)."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterator
from typing import Any

import pytest

from server.app.services.artifact_dangling_plan import (
    build_artifact_dangling_plan,
    render_artifact_dangling_plan,
)
from server.app.workflows.revision_format import serialize_definition
from server.app.workflows.schema import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
    WorkflowShardSpec,
)
from tests.fakes.storage import FakeObjectStorage

pytestmark = pytest.mark.no_db


class FakeStorage(FakeObjectStorage):
    def __init__(
        self,
        objects: dict[str, bytes] | None = None,
        *,
        head_errors: dict[str, Exception] | None = None,
    ) -> None:
        super().__init__(objects=objects)
        self.head_errors = dict(head_errors or {})
        self.head_calls: list[str] = []

    def configuration_fingerprint(self) -> dict[str, str]:
        return {
            "algorithm": "sha256",
            "bucket": "b" * 64,
            "endpoint": "e" * 64,
            "public_endpoint": "p" * 64,
            "region": "r" * 64,
            "configuration": "c" * 64,
        }

    def head_bucket(self) -> None:
        return None

    def iter_object_pages(self, prefix: str, *, page_size: int) -> Iterator[list[str]]:
        keys = sorted(key for key in self.objects if key.startswith(prefix))
        for start in range(0, len(keys), page_size):
            yield keys[start : start + page_size]

    def head_object(self, storage_key: str):
        self.head_calls.append(storage_key)
        if storage_key in self.head_errors:
            raise self.head_errors[storage_key]
        return super().head_object(storage_key)


def _row(
    key: str,
    content: bytes,
    *,
    size: int | None = None,
    content_hash: str | None = None,
) -> dict[str, Any]:
    return {
        "node_key": "producer",
        "storage_key": key,
        "size_bytes": len(content) if size is None else size,
        "content_hash": hashlib.sha256(content).hexdigest()
        if content_hash is None
        else content_hash,
        "uploaded_at": "2026-01-01T00:00:00+00:00",
    }


def _definition(
    workspace_id: str = "ws",
    *,
    producer_inputs: list[str] | None = None,
    producer_outputs: list[str] | None = None,
    consumer_inputs: list[str] | None = None,
    include_producer: bool = True,
    include_other: bool = False,
    sharded: bool = False,
) -> WorkflowDefinition:
    nodes = {}
    if include_producer:
        nodes["producer"] = WorkflowNode(
            key="producer",
            label="Producer",
            capability="produce",
            inputs=producer_inputs or [],
            outputs=["result.json"] if producer_outputs is None else producer_outputs,
            shard=WorkflowShardSpec(count=2) if sharded else None,
        )
    nodes["consumer"] = WorkflowNode(
        key="consumer",
        label="Consumer",
        capability="consume",
        after=["producer"] if include_producer else [],
        inputs=["result.json"] if consumer_inputs is None else consumer_inputs,
    )
    if include_other:
        nodes["other"] = WorkflowNode(key="other", label="Other", capability="other")
    return WorkflowDefinition(
        key=workspace_id,
        label=workspace_id,
        intake=WorkflowIntake(),
        nodes=nodes,
    )


def _artifact(
    node_key: str,
    storage_key: str,
    content: bytes,
    *,
    name: str = "result.json",
    uploaded_at: str = "2026-01-01T00:00:00+00:00",
    content_hash: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        **_row(storage_key, content, content_hash=content_hash),
        "node_key": node_key,
        "uploaded_at": uploaded_at,
    }


def _job(
    job_id: str,
    artifacts: list[dict[str, Any]],
    *,
    workspace_id: str = "ws",
    definition: WorkflowDefinition | None = None,
    statuses: dict[str, str] | None = None,
    created_at: str = "2026-01-01T00:00:00+00:00",
    execution_generation: int = 9,
    execution_mode: str = "full",
    target_node_key: str | None = None,
    pending_shard_nodes: list[str] | None = None,
    legacy: bool = False,
) -> dict[str, Any]:
    definition = definition or _definition(workspace_id)
    statuses = statuses or {"producer": "completed", "consumer": "pending"}
    definition_json = serialize_definition(definition)
    return {
        "id": job_id,
        "workspace_id": workspace_id,
        "status": "completed",
        "execution_generation": execution_generation,
        "created_at": created_at,
        "workflow_revision_id": "rev-job",
        "workflow_version": 3,
        "workflow_definition_hash": "job-hash",
        "workflow_definition_snapshot_json": "" if legacy else definition_json,
        "execution_mode": execution_mode,
        "target_node_key": target_node_key,
        "artifacts": artifacts,
        "nodes": [{"node_key": key, "status": status} for key, status in statuses.items()],
        "pending_shard_nodes": pending_shard_nodes or [],
        "active_revision": (
            {
                "id": "rev-active",
                "version": 4,
                "definition_hash": "active-hash",
                "definition_json": definition_json,
                "status": "active",
            }
            if legacy
            else None
        ),
    }


class FakeQueries:
    def __init__(self, jobs: list[dict[str, Any]]) -> None:
        self.jobs = sorted(jobs, key=lambda job: (job["created_at"], job["id"]))
        self.calls: list[dict[str, Any]] = []

    def artifact_dangling_job_page(
        self,
        *,
        after: tuple[Any, str] | None = None,
        limit: int = 100,
        workspace_ids: list[str] | None = None,
        job_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {"after": after, "limit": limit, "workspace_ids": workspace_ids, "job_ids": job_ids}
        )
        jobs = self.jobs
        if workspace_ids is not None:
            jobs = [job for job in jobs if job["workspace_id"] in workspace_ids]
        if job_ids is not None:
            jobs = [job for job in jobs if job["id"] in job_ids]
        if after is not None:
            jobs = [job for job in jobs if (job["created_at"], job["id"]) > after]
        return copy.deepcopy(jobs[:limit])


def _healthy_job(
    job_id: str = "job-control", *, empty_hash: bool = False
) -> tuple[dict, str, bytes]:
    key = f"jobs/ws/{job_id}/.v/attempt/result.json"
    content = b"healthy"
    artifact = _artifact("producer", key, content, content_hash="" if empty_hash else None)
    return (
        _job(job_id, [artifact], statuses={"producer": "completed", "consumer": "completed"}),
        key,
        content,
    )


def _plan(jobs: list[dict[str, Any]], storage: FakeStorage, **overrides: Any) -> dict[str, Any]:
    options = {"max_candidate_ratio": 1.0, **overrides}
    return build_artifact_dangling_plan(FakeQueries(jobs), storage, **options)


def test_latest_tie_complete_row_set_actionable_scope_and_stable_hash() -> None:
    older_key = "jobs/ws/job-a/.v/old/result.json"
    latest_key = "jobs/ws/job-a/.v/latest/result.json"
    candidate = _job(
        "job-a",
        [
            _artifact("node_a", older_key, b"older"),
            _artifact("node_z", latest_key, b"latest"),
        ],
    )
    control, control_key, control_bytes = _healthy_job()
    storage = FakeStorage({control_key: control_bytes})

    first = _plan([candidate, control], storage)
    second = _plan([candidate, control], FakeStorage({control_key: control_bytes}))

    assert first["health"]["passed"] is True
    assert first["summary"] == {
        "outcomes": {"object_missing": 1, "ok": 1},
        "candidate_count": 1,
        "candidate_ratio": 0.5,
        "unknown_count": 0,
        "actionable_count": 1,
        "report_entry_count": 1,
    }
    [entry] = first["artifacts"]
    assert entry["execution_generation"] == 9
    assert entry["latest_row"]["node_key"] == "node_z"
    assert [row["node_key"] for row in entry["manifest_rows"]] == ["node_a", "node_z"]
    assert entry["rerun"]["seed_node"] == "producer"
    assert entry["rerun"]["reset_closure"] == ["consumer", "producer"]
    assert entry["rerun"]["staging_output_names"] == ["result.json"]
    assert entry["rerun"]["actionable"] is True
    assert entry["positive_controls"][0]["job_id"] == "job-control"
    assert storage.head_calls.count(latest_key) == 2
    assert older_key not in storage.head_calls
    assert first["health"]["thresholds"] == {
        "max_candidates": 20,
        "max_candidate_ratio": 1.0,
        "development_defaults_only": True,
        "calibrated": False,
        "production_apply_allowed": False,
    }
    defaults = build_artifact_dangling_plan(FakeQueries([]), FakeStorage())
    assert defaults["health"]["thresholds"]["max_candidates"] == 20
    assert defaults["health"]["thresholds"]["max_candidate_ratio"] == 0.02
    assert first["recovery"]["apply_implemented"] is False
    assert first["recovery"]["production_apply_allowed"] is False
    assert render_artifact_dangling_plan(first) == render_artifact_dangling_plan(second)
    assert first["plan_sha256"] == second["plan_sha256"]


def test_positive_control_must_be_hash_verified_and_from_another_job() -> None:
    candidate_key = "jobs/ws/job-a/result.json"
    healthy_same_job = "jobs/ws/job-a/other.json"
    candidate = _job(
        "job-a",
        [
            _artifact("producer", candidate_key, b"missing"),
            _artifact("producer", healthy_same_job, b"same", name="other.json"),
        ],
    )
    control, _, _ = _healthy_job(empty_hash=True)
    storage = FakeStorage(
        {healthy_same_job: b"same", control["artifacts"][0]["storage_key"]: b"healthy"}
    )

    plan = _plan([candidate, control], storage)

    candidate_entry = next(entry for entry in plan["artifacts"] if entry["name"] == "result.json")
    assert candidate_entry["positive_controls"] == []
    assert plan["health"]["passed"] is False
    assert candidate_entry["rerun"]["actionable"] is False
    check = next(
        check
        for check in plan["health"]["checks"]
        if check["code"] == "non_candidate_positive_controls"
    )
    assert check["covered_candidates"] == 0


def test_unknown_anywhere_fuses_every_actionable_candidate() -> None:
    missing_key = "jobs/ws/job-a/result.json"
    unknown_key = "jobs/ws/job-unknown/result.json"
    candidate = _job("job-a", [_artifact("producer", missing_key, b"missing")])
    unknown = _job("job-unknown", [_artifact("producer", unknown_key, b"unknown")])
    control, control_key, control_bytes = _healthy_job()
    storage = FakeStorage(
        {control_key: control_bytes}, head_errors={unknown_key: TimeoutError("head timeout")}
    )

    plan = _plan([candidate, unknown, control], storage)

    assert plan["summary"]["unknown_count"] == 1
    assert plan["summary"]["actionable_count"] == 0
    assert plan["health"]["passed"] is False
    assert (
        next(check for check in plan["health"]["checks"] if check["code"] == "no_unknown")["passed"]
        is False
    )
    assert all(not entry["rerun"]["actionable"] for entry in plan["artifacts"])


def test_count_and_ratio_thresholds_have_structured_failures() -> None:
    candidate = _job("job-a", [_artifact("producer", "jobs/ws/job-a/result.json", b"x")])
    control, key, content = _healthy_job()
    count_plan = _plan([candidate, control], FakeStorage({key: content}), max_candidates=0)
    ratio_plan = _plan([candidate, control], FakeStorage({key: content}), max_candidate_ratio=0.0)

    count_check = next(
        check
        for check in count_plan["health"]["checks"]
        if check["code"] == "candidate_count_threshold"
    )
    ratio_check = next(
        check
        for check in ratio_plan["health"]["checks"]
        if check["code"] == "candidate_ratio_threshold"
    )
    assert count_check == {
        "code": "candidate_count_threshold",
        "status": "failed",
        "passed": False,
        "candidate_count": 1,
        "maximum": 0,
    }
    assert ratio_check["passed"] is False
    assert ratio_check["candidate_ratio"] == 0.5
    assert count_plan["summary"]["actionable_count"] == 0
    assert ratio_plan["summary"]["actionable_count"] == 0


def test_empty_list_with_db_references_is_a_distinct_fuse() -> None:
    candidate = _job("job-a", [_artifact("producer", "jobs/ws/job-a/result.json", b"x")])
    plan = _plan([candidate], FakeStorage())
    check = next(
        check
        for check in plan["health"]["checks"]
        if check["code"] == "empty_list_with_db_references"
    )
    assert check["passed"] is False
    assert check["key_count"] == 0
    assert check["referenced_key_count"] == 1
    assert plan["summary"]["actionable_count"] == 0


def test_report_only_scope_matrix() -> None:
    definitions = {
        "dormant": _definition(consumer_inputs=[]),
        "rmw": _definition(producer_inputs=["result.json"]),
        "no-producer": _definition(include_producer=False),
        "terminal": _definition(),
        "rewrite": _definition(),
        "running": _definition(sharded=True),
        "run-to": _definition(include_other=True),
    }
    jobs = []
    for index, (case, definition) in enumerate(definitions.items()):
        statuses = {"producer": "completed", "consumer": "pending"}
        if not definition.nodes.get("producer"):
            statuses = {"consumer": "pending"}
        if case == "terminal":
            statuses = {"producer": "completed", "consumer": "completed"}
        if case == "rewrite":
            statuses = {"producer": "pending", "consumer": "pending"}
        if case == "running":
            statuses = {"producer": "running", "consumer": "pending"}
        if case == "run-to":
            statuses = {"producer": "completed", "consumer": "pending", "other": "pending"}
        jobs.append(
            _job(
                f"job-{case}",
                [_artifact("producer", f"jobs/ws/job-{case}/result.json", b"bad")],
                definition=definition,
                statuses=statuses,
                created_at=f"2026-01-01T00:00:0{index}+00:00",
                execution_mode="until_node" if case == "run-to" else "full",
                target_node_key="other" if case == "run-to" else None,
                pending_shard_nodes=["producer"] if case == "running" else [],
            )
        )
    control, key, content = _healthy_job()

    plan = _plan([*jobs, control], FakeStorage({key: content}))
    entries = {entry["job_id"]: entry for entry in plan["artifacts"]}

    assert plan["health"]["passed"] is True
    assert plan["summary"]["actionable_count"] == 0
    assert "not_live_probe" in entries["job-dormant"]["rerun"]["reasons"]
    assert "rmw_or_staging_exclusion" in entries["job-rmw"]["rerun"]["reasons"]
    assert "no_current_producer" in entries["job-no-producer"]["rerun"]["reasons"]
    assert "not_live_probe" in entries["job-terminal"]["rerun"]["reasons"]
    assert "in_flight_producer_will_rewrite" in entries["job-rewrite"]["rerun"]["reasons"]
    running = entries["job-running"]["rerun"]
    assert "job_has_running_nodes" in running["reasons"]
    assert running["live"]["effective_running_nodes"] == []
    assert running["live"]["raw_running_nodes"] == ["producer"]
    assert "outside_run_to_scope" in entries["job-run-to"]["rerun"]["reasons"]
    assert all(not entry["rerun"]["actionable"] for entry in entries.values())


def test_reset_closure_outside_run_to_scope_is_report_only() -> None:
    definition = WorkflowDefinition(
        key="ws",
        label="ws",
        intake=WorkflowIntake(),
        nodes={
            "producer": WorkflowNode(
                key="producer", label="Producer", capability="produce", outputs=["result.json"]
            ),
            "outside": WorkflowNode(
                key="outside", label="Outside", capability="produce", outputs=["result.json"]
            ),
            "consumer": WorkflowNode(
                key="consumer",
                label="Consumer",
                capability="consume",
                after=["producer"],
                inputs=["result.json"],
            ),
            "outside_consumer": WorkflowNode(
                key="outside_consumer",
                label="Outside consumer",
                capability="consume",
                inputs=["result.json"],
            ),
        },
    )
    candidate = _job(
        "job-a",
        [_artifact("producer", "jobs/ws/job-a/result.json", b"x")],
        definition=definition,
        statuses={
            "producer": "completed",
            "outside": "completed",
            "consumer": "pending",
            "outside_consumer": "completed",
        },
        execution_mode="until_node",
        target_node_key="consumer",
    )
    control, key, content = _healthy_job()

    plan = _plan([candidate, control], FakeStorage({key: content}))

    [entry] = plan["artifacts"]
    assert entry["rerun"]["seed_node"] == "outside"
    assert entry["rerun"]["reset_closure_outside_run_to_scope"] == ["outside_consumer"]
    assert "reset_closure_outside_run_to_scope" in entry["rerun"]["reasons"]
    assert entry["rerun"]["actionable"] is False


def test_keyset_pagination_and_filters() -> None:
    jobs = [
        _job(
            f"job-{index}",
            [],
            workspace_id="ws-a" if index % 2 == 0 else "ws-b",
            created_at=f"2026-01-0{index + 1}T00:00:00+00:00",
        )
        for index in range(5)
    ]
    queries = FakeQueries(jobs)

    plan = build_artifact_dangling_plan(queries, FakeStorage(), page_size=2)

    assert plan["scan"]["page_count"] == 3
    assert [page["job_count"] for page in plan["scan"]["pages"]] == [2, 2, 1]
    assert plan["scan"]["job_count"] == 5
    assert queries.calls[1]["after"] == (jobs[1]["created_at"], "job-1")
    filtered = build_artifact_dangling_plan(
        FakeQueries(jobs), FakeStorage(), workspace_ids=["ws-a"], page_size=2
    )
    assert filtered["scan"]["job_count"] == 3
    assert filtered["scan"]["page_count"] == 2
    by_id = build_artifact_dangling_plan(
        FakeQueries(jobs), FakeStorage(), job_ids=["job-4", "job-4"], page_size=2
    )
    assert by_id["scan"]["job_count"] == 1
    assert by_id["scan"]["job_ids"] == ["job-4"]


def test_legacy_job_uses_only_its_workspace_active_revision() -> None:
    candidate = _job(
        "job-a",
        [_artifact("producer", "jobs/ws/job-a/result.json", b"x")],
        legacy=True,
    )
    control, key, content = _healthy_job()
    plan = _plan([candidate, control], FakeStorage({key: content}))
    [entry] = plan["artifacts"]
    assert entry["definition"]["source"] == "active_revision"
    assert entry["definition"]["active_revision_id"] == "rev-active"
    assert entry["definition"]["job_snapshot_present"] is False


def test_render_rejects_payload_mutation_after_hashing() -> None:
    plan = _plan([], FakeStorage())
    plan["summary"]["candidate_count"] = 99
    with pytest.raises(ValueError, match="plan hash"):
        render_artifact_dangling_plan(plan)


def test_plan_is_json_serializable_without_runtime_timestamps() -> None:
    plan = _plan([], FakeStorage())
    rendered = render_artifact_dangling_plan(plan)
    assert json.loads(rendered)["report"] == "job-artifact-dangling-recovery-dry-run"
    assert "scanned_at" not in rendered
    assert plan["scan"]["page_count"] == 0
    assert plan["health"]["passed"] is True
