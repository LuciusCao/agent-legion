import pytest

from server.app.executors.config import (
    LocalCapabilityConfig,
    LocalExecutorConfig,
    OpenClawCapabilityConfig,
    OpenClawExecutorConfig,
)
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.models import LeaseClaimRequest
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workspace_configuration import WorkspaceConfigurationService


@pytest.fixture
def workspace_service(job_db, settings, agent_manager):
    return WorkspaceConfigurationService(
        job_db, settings, agent_manager, WorkflowCatalogService(settings)
    )


@pytest.fixture
def workspace(workspace_service):
    return workspace_service.create(
        {"name": "Test", "default_workflow_key": "question_comprehension_info"}
    )


def test_workspace_configuration_missing_workspace_raises_domain_error(workspace_service):
    with pytest.raises(NotFoundError, match="Workspace not found"):
        workspace_service.get("missing")


def test_workspace_configuration_rejects_unknown_settings_section(workspace_service, workspace):
    with pytest.raises(NotFoundError, match="Unknown settings section"):
        workspace_service.update_section(workspace["id"], "unknown", {})


def test_replace_configuration_saves_workspace_and_executors_in_one_transaction(
    workspace_service: WorkspaceConfigurationService,
    workspace,
) -> None:
    result = workspace_service.replace_configuration(
        workspace["id"],
        workspace_patch={"name": "Reading"},
        settings_patch={"workflowKey": "question_comprehension_info"},
        executor_allocations=[{"executor_id": "local-default", "concurrency_limit": 4}],
        node_bindings=[
            {
                "workflow_key": "question_comprehension_info",
                "node_key": "fetch_questions",
                "executor_id": "local-default",
            }
        ],
        node_limits=[
            {
                "workflow_key": "question_comprehension_info",
                "node_key": "fetch_questions",
                "concurrency_limit": 2,
            }
        ],
    )
    assert result["workspace"]["name"] == "Reading"
    assert result["settings"]["workflowKey"] == "question_comprehension_info"
    assert result["executor_configuration"]["allocations"][0]["concurrency_limit"] == 4
    assert result["executor_configuration"]["bindings"][0]["node_key"] == "fetch_questions"
    assert result["executor_configuration"]["node_limits"][0]["concurrency_limit"] == 2


def test_replace_configuration_rolls_back_workspace_on_invalid_binding(
    workspace_service: WorkspaceConfigurationService,
    workspace,
) -> None:
    original_name = workspace["name"]
    with pytest.raises(InvalidOperationError):
        workspace_service.replace_configuration(
            workspace["id"],
            workspace_patch={"name": "Must Roll Back"},
            settings_patch={"workflowKey": "question_comprehension_info"},
            executor_allocations=[{"executor_id": "local-default", "concurrency_limit": 4}],
            node_bindings=[
                {
                    "workflow_key": "question_comprehension_info",
                    "node_key": "unknown_node",
                    "executor_id": "local-default",
                }
            ],
            node_limits=[],
        )
    persisted = workspace_service.get(workspace["id"])
    assert persisted["name"] == original_name
    config = workspace_service.job_db.get_workspace_executor_configuration(workspace["id"])
    assert config["allocations"] == []
    assert config["bindings"] == []
    assert config["node_limits"] == []


def test_workspace_configuration_update_delegates(workspace_service, workspace):
    updated = workspace_service.update(workspace["id"], {"description": "New description"})
    assert updated["description"] == "New description"


def test_workspace_configuration_settings_payload(workspace_service, workspace):
    payload = workspace_service.settings_payload(workspace["id"])
    assert payload["workflowKey"] == "question_comprehension_info"


def test_executor_stats_report_configured_capacity_and_leases(
    workspace_service, workspace, job_db, settings, monkeypatch
):
    executor_definitions = dict(settings.executor_definitions)
    executor_definitions["openclaw-main"] = OpenClawExecutorConfig(
        kind="openclaw",
        agent_id="main",
        global_capacity=8,
        capabilities={"review": OpenClawCapabilityConfig(skill="review-interactions")},
    )
    monkeypatch.setattr(settings, "executor_definitions", executor_definitions)

    job_db.replace_workspace_executor_configuration(
        workspace["id"],
        allocations=[
            {"executor_id": "local-default", "concurrency_limit": 4},
            {"executor_id": "openclaw-main", "concurrency_limit": 2},
        ],
        bindings=[
            {
                "workflow_key": "question_comprehension_info",
                "node_key": "fetch",
                "executor_id": "local-default",
            },
            {
                "workflow_key": "question_comprehension_info",
                "node_key": "review",
                "executor_id": "openclaw-main",
            },
        ],
        node_limits=[],
    )

    jobs = []
    for i in range(3):
        job = job_db.create_job(
            workflow_key="question_comprehension_info",
            source_type="question",
            source_id=f"src-{i}",
            batch_id="",
            title=f"Job {i}",
            node_keys=["fetch", "extract", "review"],
            workspace_id=workspace["id"],
        )
        jobs.append(job)

    repo = ExecutorLeaseRepository(job_db.path)
    claim_specs = [
        ("local-default", "fetch", "fetch_questions", 16, None),
        ("openclaw-main", "review", "review", 8, None),
    ]
    for i, (executor_id, node_key, capability, global_capacity, local_limit) in enumerate(
        claim_specs
    ):
        claim = repo.try_claim(
            LeaseClaimRequest(
                executor_id=executor_id,
                global_capacity=global_capacity,
                workspace_id=workspace["id"],
                job_id=jobs[i]["id"],
                workflow_key="question_comprehension_info",
                node_key=node_key,
                capability=capability,
                local_node_limit=local_limit,
                lease_ttl_seconds=60,
                log_path=str(settings.logs_dir / "run.log"),
            )
        )
        assert claim is not None, f"claim failed for {executor_id}"

    stats = workspace_service.stats(workspace["id"])
    executor_status = stats["executor_status"]
    executors = {e["executor_id"]: e for e in executor_status["executors"]}

    assert executors["local-default"]["kind"] == "local"
    assert executors["local-default"]["global_capacity"] == 128
    assert executors["local-default"]["workspace_limit"] == 4
    assert executors["local-default"]["running"] == 1
    assert executors["local-default"]["available"] == 3

    assert executors["openclaw-main"]["kind"] == "openclaw"
    assert executors["openclaw-main"]["global_capacity"] == 8
    assert executors["openclaw-main"]["workspace_limit"] == 2
    assert executors["openclaw-main"]["running"] == 1
    assert executors["openclaw-main"]["available"] == 1


def test_executor_stats_does_not_consult_agent_status_manager(
    workspace_service, workspace, job_db, settings, monkeypatch
):
    executor_definitions = dict(settings.executor_definitions)
    executor_definitions["local-default"] = LocalExecutorConfig(
        kind="local",
        global_capacity=4,
        capabilities={"fetch": LocalCapabilityConfig(handler="x")},
    )
    monkeypatch.setattr(settings, "executor_definitions", executor_definitions)

    job_db.replace_workspace_executor_configuration(
        workspace["id"],
        allocations=[{"executor_id": "local-default", "concurrency_limit": 2}],
        bindings=[
            {
                "workflow_key": "question_comprehension_info",
                "node_key": "fetch",
                "executor_id": "local-default",
            }
        ],
        node_limits=[],
    )

    consulted = []
    original_get_allowed = workspace_service.agent_manager.get_allowed_agents
    original_get_all = workspace_service.agent_manager.get_all

    def tracking_get_allowed_agents(workspace_id_arg):
        consulted.append(("get_allowed_agents", workspace_id_arg))
        return original_get_allowed(workspace_id_arg)

    def tracking_get_all():
        consulted.append(("get_all",))
        return original_get_all()

    monkeypatch.setattr(
        workspace_service.agent_manager, "get_allowed_agents", tracking_get_allowed_agents
    )
    monkeypatch.setattr(workspace_service.agent_manager, "get_all", tracking_get_all)

    stats = workspace_service.stats(workspace["id"])
    assert "executor_status" in stats
    assert not consulted, "stats() should not consult AgentStatusManager"


def test_executor_stats_available_respects_global_usage_by_other_workspaces(
    workspace_service, workspace, job_db, settings
):
    other = workspace_service.create(
        {"name": "Other", "default_workflow_key": "question_comprehension_info"}
    )
    for workspace_id, limit in ((workspace["id"], 8), (other["id"], 128)):
        job_db.replace_workspace_executor_configuration(
            workspace_id,
            allocations=[{"executor_id": "local-default", "concurrency_limit": limit}],
            bindings=[
                {
                    "workflow_key": "question_comprehension_info",
                    "node_key": "fetch",
                    "executor_id": "local-default",
                }
            ],
            node_limits=[],
        )

    repo = ExecutorLeaseRepository(job_db.path)
    for i in range(128):
        owner = other
        job = job_db.create_job(
            workflow_key="question_comprehension_info",
            source_type="question",
            source_id=f"global-{i}",
            batch_id="",
            title=f"Global {i}",
            node_keys=["fetch"],
            workspace_id=owner["id"],
        )
        claim = repo.try_claim(
            LeaseClaimRequest(
                executor_id="local-default",
                global_capacity=128,
                workspace_id=owner["id"],
                job_id=job["id"],
                workflow_key="question_comprehension_info",
                node_key="fetch",
                capability="fetch_questions",
                local_node_limit=None,
                lease_ttl_seconds=60,
                log_path=str(settings.logs_dir / "run.log"),
            )
        )
        assert claim is not None

    stats = workspace_service.stats(workspace["id"])
    status = stats["executor_status"]["executors"][0]
    assert status["running"] == 0
    assert status["available"] == 0


def test_create_workspace_seeds_active_workflow_revision(workspace_service, job_db):
    workspace = workspace_service.create(
        {"name": "WS", "default_workflow_key": "question_comprehension_info"}
    )
    active = job_db.get_active_workflow_revision(workspace["id"], "question_comprehension_info")
    assert active is not None
    assert active["version"] == 1


def _save(workspace_service, workspace_id: str, agent_capacity: int | None = None):
    return workspace_service.replace_configuration(
        workspace_id,
        workspace_patch={},
        settings_patch={"workflowKey": "question_comprehension_info"},
        executor_allocations=[],
        node_bindings=[],
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
        {"name": "WS", "default_workflow_key": "question_comprehension_info"}
    )
    workspace_service.update_section(
        workspace["id"], "workflow", {"workflowKey": "video_knowledge"}
    )
    assert job_db.get_active_workflow_revision(workspace["id"], "video_knowledge") is not None
