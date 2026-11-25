import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import ExecutionResult, LeaseClaimRequest
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_batch_operations import JobBatchOpsFeeder, dispatch_rerun_by_failure
from server.app.services.job_batch_operations.handlers import BatchOpsExecutors, run_chunk
from server.app.services.job_rerun import JobRerunService
from server.app.services.job_rerun.preview import batch_rerun_preview
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.helpers import load_builtin_definition, publish_builtin_revision

NODE_KEYS = [
    "intake_knowledge_points",
    "write_script",
    "review_script",
    "publish_content",
]


@pytest.fixture
def rerun_service(job_db, settings):
    return JobRerunService(
        job_db,
        ExecutorLeaseRepository(job_db, data_dir=settings.data_dir),
        settings,
        JobArtifactMutationService(settings.jobs_dir),
    )


@pytest.fixture
def workspace(job_db):
    created = job_db.create_workspace("default")
    publish_builtin_revision(job_db, created["id"])
    return created


def _create_job(job_db, workspace, source_id: str) -> dict[str, Any]:
    return job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id=source_id,
        run_id="",
        title=f"Question {source_id}",
        node_keys=list(NODE_KEYS),
        workspace_id=workspace["id"],
    )


def _fail_node(
    job_db,
    job: dict[str, Any],
    node_key: str,
    category: str,
    detail: str,
    error_message: str = "boom",
) -> None:
    run = job_db.start_node_run(job["id"], node_key, ["cmd"], f"logs/{job['id']}-{node_key}.log")
    assert run is not None
    with job_db.connect() as conn:
        conn.execute(
            """
            update node_runs
            set status='failed', error_message=%s, failure_category=%s, failure_detail=%s,
                finished_at=current_timestamp
            where id=%s
            """,
            (error_message, category, detail, run["id"]),
        )
        conn.execute(
            "update job_nodes set status='failed', error_message=%s where job_id=%s and node_key=%s",
            (error_message, job["id"], node_key),
        )
        conn.execute("update jobs set status='failed' where id=%s", (job["id"],))
        conn.execute("commit")


def _node_statuses(job_db, job_id: str) -> dict[str, str]:
    return {n["node_key"]: n["status"] for n in job_db.list_job_nodes(job_id)}


def test_filters_by_failure_category(rerun_service, job_db, workspace):
    technical_job = _create_job(job_db, workspace, "Q-tech")
    business_job = _create_job(job_db, workspace, "Q-biz")
    _fail_node(job_db, technical_job, "write_script", "technical", "provider_stream")
    _fail_node(job_db, business_job, "publish_content", "business", "review_rejected")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "technical")

    assert [r["job_id"] for r in results] == [technical_job["id"]]
    assert results[0]["status"] == "succeeded"
    assert _node_statuses(job_db, technical_job["id"])["write_script"] == "pending"
    assert _node_statuses(job_db, business_job["id"])["publish_content"] == "failed"


def test_rerun_self_marks_node_pending_and_downstream_stale(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-self")
    _fail_node(job_db, job, "write_script", "technical", "timeout")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "technical")

    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == ["write_script"]
    nodes = _node_statuses(job_db, job["id"])
    assert nodes["write_script"] == "pending"
    assert nodes["review_script"] == "stale"
    assert nodes["publish_content"] == "stale"


def test_provider_auth_failure_is_selected_for_manual_technical_rerun(
    rerun_service, job_db, workspace
):
    job = _create_job(job_db, workspace, "Q-provider-auth")
    node_key = "intake_knowledge_points"
    claim = rerun_service.lease_repo.try_claim(
        LeaseClaimRequest(
            executor_id="code",
            global_capacity=16,
            workspace_id=workspace["id"],
            job_id=job["id"],
            workflow_key="education_video_problems_generation",
            node_key=node_key,
            capability=node_key,
            local_node_limit=None,
            lease_ttl_seconds=60,
            log_path="logs/provider-auth.log",
        )
    )
    assert claim is not None
    message = (
        "provider call failed: HTTP 200 error body: code=401: Authorization apikey is disabled"
    )
    assert rerun_service.lease_repo.finish(
        claim.lease_id, ExecutionResult(status="failed", exit_code=1, error_message=message)
    )

    results = rerun_service.rerun_by_failure_category(workspace["id"], "technical")

    assert [r["job_id"] for r in results] == [job["id"]]
    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == [node_key]
    assert _node_statuses(job_db, job["id"])[node_key] == "pending"


def test_business_auto_reruns_direct_upstream(rerun_service, job_db, workspace):
    """rerun_upstream 走合并上游（#759）：显式边上游 ∪ 输入产物的隐式
    生产者——review_script 的直接生产者是 write_script（script.md）与
    intake_knowledge_points（knowledge_point.json）。"""
    job = _create_job(job_db, workspace, "Q-upstream")
    _fail_node(job_db, job, "review_script", "business", "review_rejected")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "business")

    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == ["intake_knowledge_points", "write_script"]
    nodes = _node_statuses(job_db, job["id"])
    assert nodes["intake_knowledge_points"] == "pending"
    assert nodes["write_script"] == "pending"
    assert nodes["review_script"] == "stale"


def test_explicit_rerun_self_strategy_on_business_category(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-strategy")
    _fail_node(job_db, job, "publish_content", "business", "review_rejected")

    results = rerun_service.rerun_by_failure_category(
        workspace["id"], "business", strategy="rerun_self"
    )

    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == ["publish_content"]


def test_unknown_category_defaults_to_rerun_self(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-unknown")
    _fail_node(job_db, job, "review_script", "unknown", "unknown")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "unknown")

    assert results[0]["status"] == "succeeded"
    assert results[0]["rerun_nodes"] == ["review_script"]


def test_busy_job_is_skipped(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-busy")
    _fail_node(job_db, job, "intake_knowledge_points", "technical", "provider_stream")
    job_db.update_job_node(job["id"], "write_script", status="running")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "technical")

    assert results[0]["status"] == "skipped"
    assert results[0]["reason_code"] == "busy"
    assert results[0]["rerun_nodes"] == []
    assert _node_statuses(job_db, job["id"])["intake_knowledge_points"] == "failed"


def test_job_ids_filter_and_no_match_reporting(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-listed")
    other = _create_job(job_db, workspace, "Q-unlisted")
    _fail_node(job_db, job, "write_script", "technical", "timeout")
    _fail_node(job_db, other, "write_script", "technical", "timeout")

    results = rerun_service.rerun_by_failure_category(
        workspace["id"], "technical", job_ids=[job["id"], "missing-job"]
    )

    by_id = {r["job_id"]: r for r in results}
    assert by_id[job["id"]]["status"] == "succeeded"
    assert by_id["missing-job"]["status"] == "skipped"
    assert by_id["missing-job"]["reason_code"] == "no_matching_failure"
    assert other["id"] not in by_id
    assert _node_statuses(job_db, other["id"])["write_script"] == "failed"


def test_workflow_key_argument_is_inert_after_read_binding(rerun_service, job_db, workspace):
    """#211 Phase 3（#307）：读法谓词只绑 workspace_id——服务层 workflow_key
    参数不再过滤（v62 恒等，错 key 由路由层守卫 400 挡截）。缺省与恒等值等价。"""
    job = _create_job(job_db, workspace, "Q-wf")
    _fail_node(job_db, job, "write_script", "technical", "timeout")

    absent = rerun_service.rerun_by_failure_category(workspace["id"], "technical")
    equal = rerun_service.rerun_by_failure_category(
        workspace["id"], "technical", workflow_key=str(workspace["id"])
    )

    assert [r["job_id"] for r in absent] == [job["id"]]
    assert [r["job_id"] for r in equal] == [job["id"]]


def test_recovered_node_is_not_rerun_again(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-recovered")
    _fail_node(job_db, job, "write_script", "technical", "timeout")
    # A newer completed run supersedes the failure for that node.
    with job_db.connect() as conn:
        conn.execute(
            """
            insert into node_runs(job_id, node_key, status, started_at, finished_at)
            values (%s, 'write_script', 'completed', current_timestamp, current_timestamp)
            """,
            (job["id"],),
        )
        conn.execute("commit")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "technical")

    assert results == []


def test_rerun_clears_failure_fields_on_job_nodes(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-clear")
    _fail_node(
        job_db,
        job,
        "write_script",
        "technical",
        "stale_definition",
        error_message=(
            "Agent definition 'generator-v1' was disabled or changed while the request was queued"
        ),
    )
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set failure_category='technical',"
            " failure_detail='stale_definition' where job_id=%s and node_key=%s",
            (job["id"], "write_script"),
        )
        conn.execute("commit")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "technical")

    assert results[0]["status"] == "succeeded"
    node = job_db.get_job_node(job["id"], "write_script")
    assert node["status"] == "pending"
    assert node["failure_category"] == ""
    assert node["failure_detail"] == ""


def _policy_workspace(job_db, policies: dict[str, dict[str, str]]):
    workspace = job_db.create_workspace("failure-policy")
    definition = load_builtin_definition("education_video_problems_generation")
    nodes = dict(definition.nodes)
    for node_key, on_failure in policies.items():
        nodes[node_key] = replace(nodes[node_key], on_failure=on_failure)
    WorkflowRevisionService(job_db).ensure_active_revision(
        workspace["id"], replace(definition, nodes=nodes)
    )
    return workspace


def _feeder_executors(rerun_service: JobRerunService) -> BatchOpsExecutors:
    return BatchOpsExecutors(
        rerun=rerun_service,
        deletion=SimpleNamespace(),
        execution=SimpleNamespace(),
        pause=SimpleNamespace(),
        upgrade=SimpleNamespace(),
        packages=SimpleNamespace(),
        approvals=SimpleNamespace(),
    )


def test_auto_resolves_each_failed_node_declaration_and_dedupes_targets(rerun_service, job_db):
    workspace = _policy_workspace(
        job_db,
        {
            "review_script": {"business": "rerun_upstream"},
            "write_script": {"business": "rerun_self"},
        },
    )
    job = _create_job(job_db, workspace, "Q-mixed-policy")
    _fail_node(job_db, job, "review_script", "business", "review_rejected")
    _fail_node(job_db, job, "write_script", "business", "output_invalid")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "business")

    assert set(results[0]["rerun_nodes"]) == {
        "intake_knowledge_points",
        "write_script",
    }
    assert _node_statuses(job_db, job["id"])["review_script"] == "stale"


def test_auto_root_upstream_falls_back_to_self(rerun_service, job_db):
    workspace = _policy_workspace(
        job_db, {"intake_knowledge_points": {"business": "rerun_upstream"}}
    )
    job = _create_job(job_db, workspace, "Q-root-policy")
    _fail_node(job_db, job, "intake_knowledge_points", "business", "source_missing")

    results = rerun_service.rerun_by_failure_category(workspace["id"], "business")

    assert results[0]["rerun_nodes"] == ["intake_knowledge_points"]


def test_explicit_strategy_and_from_node_override_declarations_in_preview_and_execute(
    rerun_service, job_db
):
    workspace = _policy_workspace(job_db, {"review_script": {"business": "rerun_upstream"}})
    strategy_job = _create_job(job_db, workspace, "Q-explicit-strategy")
    _fail_node(job_db, strategy_job, "review_script", "business", "review_rejected")
    preview = batch_rerun_preview(
        rerun_service,
        workspace["id"],
        [strategy_job["id"]],
        failure_category="business",
        strategy="rerun_self",
    )
    assert preview["plans"] == [
        {"job_id": strategy_job["id"], "planned_targets": ["review_script"]}
    ]
    executed = rerun_service.rerun_by_failure_category(
        workspace["id"], "business", strategy="rerun_self", job_ids=[strategy_job["id"]]
    )
    assert executed[0]["rerun_nodes"] == preview["plans"][0]["planned_targets"]

    from_node_job = _create_job(job_db, workspace, "Q-explicit-from-node")
    _fail_node(job_db, from_node_job, "review_script", "business", "review_rejected")
    preview = batch_rerun_preview(
        rerun_service,
        workspace["id"],
        [from_node_job["id"]],
        failure_category="business",
        from_node_key="write_script",
    )
    assert preview["plans"] == [
        {"job_id": from_node_job["id"], "planned_targets": ["write_script"]}
    ]
    executed = rerun_service.rerun_by_failure_category(
        workspace["id"],
        "business",
        job_ids=[from_node_job["id"]],
        from_node_key="write_script",
    )
    assert executed[0]["rerun_nodes"] == preview["plans"][0]["planned_targets"]


def test_preview_and_async_feeder_share_auto_targets_with_execute(rerun_service, job_db):
    workspace = _policy_workspace(job_db, {"review_script": {"business": "rerun_upstream"}})
    job = _create_job(job_db, workspace, "Q-preview-identity")
    _fail_node(job_db, job, "review_script", "business", "review_rejected")

    preview = batch_rerun_preview(
        rerun_service, workspace["id"], [job["id"]], failure_category="business"
    )
    fed = run_chunk(
        _feeder_executors(rerun_service),
        workspace["id"],
        "rerun_by_failure",
        [job["id"]],
        {"category": "business", "strategy": "auto"},
    )

    expected = ["intake_knowledge_points", "write_script"]
    assert preview == {
        "total_count": 1,
        "eligible_count": 1,
        "plans": [{"job_id": job["id"], "planned_targets": expected}],
    }
    assert fed[0]["rerun_nodes"] == expected
    assert _node_statuses(job_db, job["id"])["review_script"] == "stale"


def test_busy_job_has_no_preview_targets_and_execute_does_not_mutate(rerun_service, job_db):
    workspace = _policy_workspace(job_db, {"review_script": {"business": "rerun_upstream"}})
    job = _create_job(job_db, workspace, "Q-preview-busy")
    _fail_node(job_db, job, "review_script", "business", "review_rejected")
    job_db.update_job_node(job["id"], "intake_knowledge_points", status="running")

    preview = batch_rerun_preview(
        rerun_service, workspace["id"], [job["id"]], failure_category="business"
    )
    executed = rerun_service.rerun_by_failure_category(
        workspace["id"], "business", job_ids=[job["id"]]
    )

    assert preview["eligible_count"] == 0
    assert preview["plans"] == [{"job_id": job["id"], "planned_targets": []}]
    assert executed[0]["status"] == "skipped"
    assert executed[0]["reason_code"] == "busy"
    assert _node_statuses(job_db, job["id"])["review_script"] == "failed"


def test_from_node_without_matching_failure_result_contract(rerun_service, job_db, workspace):
    job = _create_job(job_db, workspace, "Q-no-match-contract")
    _fail_node(job_db, job, "review_script", "business", "review_rejected")

    results = rerun_service.rerun_by_failure_category(
        workspace["id"],
        "business",
        job_ids=[job["id"]],
        from_node_key="publish_content",
    )

    assert results == [
        {
            "job_id": job["id"],
            "operation": "rerun",
            "status": "skipped",
            "node_key": None,
            "reason_code": "no_matching_failure",
            "message": "No latest failed run at or downstream of node 'publish_content'",
            "rerun_nodes": [],
        }
    ]


def test_from_node_without_matching_failure_async_result_contract(
    rerun_service, job_db, workspace, settings, monkeypatch
):
    jobs = [_create_job(job_db, workspace, f"Q-no-match-async-{index}") for index in range(2)]
    for job in jobs:
        _fail_node(job_db, job, "review_script", "business", "review_rejected")
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.service.BATCH_OPS_ASYNC_THRESHOLD", 1
    )

    _, operation = dispatch_rerun_by_failure(
        job_db,
        workspace["id"],
        category="business",
        strategy="auto",
        from_node_key="publish_content",
        job_ids=[job["id"] for job in jobs],
        job_filter=None,
        exclude_ids=(),
        created_by="user:tester",
    )
    assert operation is not None
    feeder = JobBatchOpsFeeder(job_db, settings, _feeder_executors(rerun_service))
    for _ in range(100):
        if not feeder.consume_once():
            break
    else:
        raise AssertionError("feeder did not quiesce")

    rows = job_db.list_job_batch_operation_results_page(str(operation["id"]), limit=10)
    assert len(rows) == 2
    for row in rows:
        assert row["status"] == "skipped"
        assert row["reason_code"] == "no_matching_failure"
        assert row["message"] == "No latest failed run at or downstream of node 'publish_content'"
        payload = json.loads(str(row["payload_json"]))
        assert payload["node_key"] is None
        assert payload["reason_code"] == "no_matching_failure"
        assert payload["message"] == row["message"]
        assert payload["rerun_nodes"] == []
