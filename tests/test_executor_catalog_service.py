import pytest

from server.app.services.executor_catalog import ExecutorCatalogService
from server.app.services.executor_definition_service import (
    ExecutorDefinitionService,
    reset_published_executor_cache,
)
from tests.helpers import seed_workspace_agent_definitions


@pytest.fixture
def workspace_id(job_db) -> str:
    return job_db.create_workspace("Catalog WS", default_workflow_key="demo_workflow")["id"]


@pytest.fixture
def service(job_db, settings, agent_manager):
    return ExecutorCatalogService(settings)


def test_catalog_exposes_normalized_yaml_definitions(
    service: ExecutorCatalogService, workspace_id: str
) -> None:
    result = service.catalog(workspace_id)
    assert result["executors"] == [
        {
            "id": "code-default",
            "kind": "code",
            "global_capacity": 16,
            "capabilities": [
                "intake_knowledge_points",
                "publish_content",
            ],
            "capability_details": [
                {"name": "intake_knowledge_points"},
                {"name": "publish_content"},
            ],
        }
    ]


def test_executor_catalog_does_not_expose_agent_runtimes(
    service: ExecutorCatalogService, workspace_id: str
) -> None:
    result = service.catalog(workspace_id)
    executors_by_id = {executor["id"]: executor for executor in result["executors"]}

    assert "pi-video-main" not in executors_by_id
    assert "pi-default" not in executors_by_id
    assert "pi" not in executors_by_id
    assert set(executors_by_id) == {"code-default"}


def test_catalog_exposes_published_agent_definitions(
    service: ExecutorCatalogService, workspace_id: str
) -> None:
    # Agent 目录是 workspace 作用域（schema v46）：把 demo 模板播进本 workspace。
    seed_workspace_agent_definitions(workspace_id)
    result = service.catalog(workspace_id)
    agents_by_id = {agent["id"]: agent for agent in result["agents"]}

    agent = agents_by_id["example-review-questions-v1"]
    assert agent["runtime"] == "velites"
    assert agent["capability"] == "review_questions"
    assert agent["skill"] == "education-video-problems-generation/review-questions"
    assert agent["tools"] == ["read", "write", "bash"]
    # 全局 provider/model/thinking 投影已退役：执行默认走 workspace agentDefaults。
    assert "provider" not in agent
    assert "model" not in agent
    assert "thinking" not in agent


def test_catalog_reflects_db_published_edits(
    service: ExecutorCatalogService, job_db, settings, workspace_id: str
) -> None:
    """Catalog reads the DB published rows: an admin edit shows up without restart."""
    definitions = ExecutorDefinitionService(job_db.path)
    edited = {
        "kind": "code",
        "global_capacity": 4,
        "capabilities": {"publish_content": {}},
    }
    definitions.save_draft("code-default", edited, "user:admin")
    definitions.publish("code-default")
    # TRUNCATE isolation may leave a stale TTL entry from an earlier test.
    reset_published_executor_cache()

    result = service.catalog(workspace_id)

    executors_by_id = {executor["id"]: executor for executor in result["executors"]}
    assert executors_by_id["code-default"]["global_capacity"] == 4
    assert executors_by_id["code-default"]["capabilities"] == ["publish_content"]
