import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import LeaseClaimRequest
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.workspace_configuration import WorkspaceConfigurationService


@pytest.fixture
def workspace_service(job_db, settings):
    return WorkspaceConfigurationService(job_db, settings)


@pytest.fixture
def workspace(workspace_service):
    return workspace_service.create(
        {"name": "Test", "default_workflow_key": "education_video_problems_generation"}
    )


def test_workspace_configuration_missing_workspace_raises_domain_error(workspace_service):
    with pytest.raises(NotFoundError, match="Workspace not found"):
        workspace_service.get("missing")


def test_workspace_configuration_rejects_unknown_settings_section(workspace_service, workspace):
    with pytest.raises(NotFoundError, match="Unknown settings section"):
        workspace_service.update_section(workspace["id"], "unknown", {})


def test_replace_configuration_saves_workspace_and_node_limits_in_one_transaction(
    workspace_service: WorkspaceConfigurationService,
    workspace,
) -> None:
    # P-0.5：allocations/bindings 已随 executor 概念退役，只剩节点并发上限。
    result = workspace_service.replace_configuration(
        workspace["id"],
        workspace_patch={"name": "Reading"},
        settings_patch={"workflowKey": "education_video_problems_generation"},
        node_limits=[
            {
                "workflow_key": "education_video_problems_generation",
                "node_key": "publish_content",
                "concurrency_limit": 2,
            }
        ],
    )
    assert result["workspace"]["name"] == "Reading"
    assert result["settings"]["workflowKey"] == "education_video_problems_generation"
    assert result["executor_configuration"]["node_limits"][0]["concurrency_limit"] == 2


def test_replace_configuration_rolls_back_workspace_on_invalid_node_limit(
    workspace_service: WorkspaceConfigurationService,
    workspace,
) -> None:
    original_name = workspace["name"]
    with pytest.raises(InvalidOperationError):
        workspace_service.replace_configuration(
            workspace["id"],
            workspace_patch={"name": "Must Roll Back"},
            settings_patch={"workflowKey": "education_video_problems_generation"},
            node_limits=[
                {
                    "workflow_key": "education_video_problems_generation",
                    "node_key": "unknown_node",
                    "concurrency_limit": 1,
                }
            ],
        )
    persisted = workspace_service.get(workspace["id"])
    assert persisted["name"] == original_name
    assert workspace_service.job_db.get_workspace_node_limits(workspace["id"]) == []


def test_workspace_configuration_update_delegates(workspace_service, workspace):
    updated = workspace_service.update(workspace["id"], {"description": "New description"})
    assert updated["description"] == "New description"


def test_workspace_configuration_settings_payload(workspace_service, workspace):
    payload = workspace_service.settings_payload(workspace["id"])
    assert payload["workflowKey"] == "education_video_problems_generation"


def _claim_code_lease(job_db, workspace_id: str, job_id: str, settings, capacity: int = 16):
    repo = ExecutorLeaseRepository(job_db.path, data_dir=job_db.jobs_dir.parent)
    claim = repo.try_claim(
        LeaseClaimRequest(
            executor_id="code",
            global_capacity=capacity,
            workspace_id=workspace_id,
            job_id=job_id,
            workflow_key="education_video_problems_generation",
            node_key="fetch",
            capability="fetch",
            local_node_limit=None,
            lease_ttl_seconds=60,
            log_path=str(settings.logs_dir / "run.log"),
        )
    )
    assert claim is not None
    return claim


def _code_job(job_db, workspace_id: str, source_id: str):
    return job_db.create_job(
        workflow_key="education_video_problems_generation",
        source_type="question",
        source_id=source_id,
        run_id="",
        title=f"Job {source_id}",
        node_keys=["fetch"],
        workspace_id=workspace_id,
    )


def test_code_pool_stats_report_capacity_and_leases(workspace_service, workspace, job_db, settings):
    job = _code_job(job_db, workspace["id"], "s1")
    _claim_code_lease(job_db, workspace["id"], job["id"], settings)

    stats = workspace_service.stats(workspace["id"])
    # P-0.5：单一隐含 code 池；容量来自实例设置，running 是本工作区在跑数，
    # available 是全局剩余。
    pool = stats["code_pool"]
    assert pool == {"capacity": 16, "running": 1, "available": 15}


def test_code_pool_stats_available_respects_global_usage_by_other_workspaces(
    workspace_service, workspace, job_db, settings
):
    other = workspace_service.create(
        {"name": "Other", "default_workflow_key": "education_video_problems_generation"}
    )
    for i in range(16):
        job = _code_job(job_db, other["id"], f"global-{i}")
        _claim_code_lease(job_db, other["id"], job["id"], settings)

    stats = workspace_service.stats(workspace["id"])
    pool = stats["code_pool"]
    assert pool["running"] == 0
    assert pool["available"] == 0


def test_create_workspace_seeds_active_workflow_revision(workspace_service, job_db):
    workspace = workspace_service.create(
        {"name": "WS", "default_workflow_key": "education_video_problems_generation"}
    )
    active = job_db.get_active_workflow_revision(
        workspace["id"], "education_video_problems_generation"
    )
    assert active is not None
    assert active["version"] == 1


def _save(workspace_service, workspace_id: str, agent_capacity: int | None = None):
    return workspace_service.replace_configuration(
        workspace_id,
        workspace_patch={},
        settings_patch={"workflowKey": "education_video_problems_generation"},
        node_limits=[],
        agent_capacity=agent_capacity,
    )


def test_agent_capacity_defaults_to_unset(workspace_service, workspace):
    result = _save(workspace_service, workspace["id"])
    assert result["agent_capacity"] is None
    assert workspace_service.job_db.get_workspace_agent_capacity(workspace["id"]) is None


def test_replace_configuration_saves_agent_capacity_and_omission_leaves_it(
    workspace_service, workspace
):
    result = _save(workspace_service, workspace["id"], agent_capacity=6)
    assert result["agent_capacity"] == 6
    assert workspace_service.job_db.get_workspace_agent_capacity(workspace["id"]) == 6

    unchanged = _save(workspace_service, workspace["id"])
    assert unchanged["agent_capacity"] == 6


def test_replace_configuration_rejects_non_positive_agent_capacity(workspace_service, workspace):
    with pytest.raises(InvalidOperationError, match="Agent capacity"):
        _save(workspace_service, workspace["id"], agent_capacity=0)
    assert workspace_service.job_db.get_workspace_agent_capacity(workspace["id"]) is None


def test_update_workflow_seeds_revision_for_new_workflow(workspace_service, job_db):
    workspace = workspace_service.create(
        {"name": "WS", "default_workflow_key": "education_video_problems_generation"}
    )
    workspace_service.update_section(
        workspace["id"], "workflow", {"workflowKey": "education_video_problems_generation"}
    )
    assert (
        job_db.get_active_workflow_revision(workspace["id"], "education_video_problems_generation")
        is not None
    )
