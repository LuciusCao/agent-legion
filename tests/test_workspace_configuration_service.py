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
from server.app.services.pipeline_catalog import PipelineCatalogService
from server.app.services.workspace_configuration import WorkspaceConfigurationService


@pytest.fixture
def workspace_service(job_db, settings, agent_manager):
    return WorkspaceConfigurationService(
        job_db, settings, agent_manager, PipelineCatalogService(settings)
    )


@pytest.fixture
def workspace(workspace_service):
    return workspace_service.create({"name": "Test", "default_pipeline_key": "question_content"})


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
        settings_patch={"pipelineKey": "reading_analysis"},
        executor_allocations=[{"executor_id": "local-default", "concurrency_limit": 4}],
        node_bindings=[
            {
                "pipeline_key": "reading_analysis",
                "node_key": "fetch_questions",
                "executor_id": "local-default",
            }
        ],
        node_limits=[
            {
                "pipeline_key": "reading_analysis",
                "node_key": "fetch_questions",
                "concurrency_limit": 2,
            }
        ],
    )
    assert result["workspace"]["name"] == "Reading"
    assert result["settings"]["pipelineKey"] == "reading_analysis"
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
            settings_patch={"pipelineKey": "reading_analysis"},
            executor_allocations=[{"executor_id": "local-default", "concurrency_limit": 4}],
            node_bindings=[
                {
                    "pipeline_key": "reading_analysis",
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
    assert payload["pipelineKey"] == "question_content"


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
            {"executor_id": "pi-default", "concurrency_limit": 6},
            {"executor_id": "openclaw-main", "concurrency_limit": 2},
        ],
        bindings=[
            {
                "pipeline_key": "question_content",
                "node_key": "fetch",
                "executor_id": "local-default",
            },
            {
                "pipeline_key": "question_content",
                "node_key": "extract",
                "executor_id": "pi-default",
            },
            {
                "pipeline_key": "question_content",
                "node_key": "review",
                "executor_id": "openclaw-main",
            },
        ],
        node_limits=[],
    )

    jobs = []
    for i in range(3):
        job = job_db.create_job(
            pipeline_key="question_content",
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
        ("pi-default", "extract", "extract_keywords", 20, None),
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
                pipeline_key="question_content",
                node_key=node_key,
                capability=capability,
                local_node_limit=local_limit,
                lease_ttl_seconds=60,
                log_path="/tmp/run.log",
            )
        )
        assert claim is not None, f"claim failed for {executor_id}"

    stats = workspace_service.stats(workspace["id"])
    executor_status = stats["executor_status"]
    executors = {e["executor_id"]: e for e in executor_status["executors"]}

    assert executors["local-default"]["kind"] == "local"
    assert executors["local-default"]["global_capacity"] == 16
    assert executors["local-default"]["workspace_limit"] == 4
    assert executors["local-default"]["running"] == 1
    assert executors["local-default"]["available"] == 3

    assert executors["pi-default"]["kind"] == "pi"
    assert executors["pi-default"]["global_capacity"] == 20
    assert executors["pi-default"]["workspace_limit"] == 6
    assert executors["pi-default"]["running"] == 1
    assert executors["pi-default"]["available"] == 5

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
                "pipeline_key": "question_content",
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
