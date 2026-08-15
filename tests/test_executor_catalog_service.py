import pytest

from server.app.services.executor_catalog import ExecutorCatalogService
from server.app.services.executor_definition_service import (
    ExecutorDefinitionService,
    reset_published_executor_cache,
)


@pytest.fixture
def service(job_db, settings, agent_manager):
    return ExecutorCatalogService(settings)


def test_catalog_exposes_normalized_yaml_definitions(service: ExecutorCatalogService) -> None:
    result = service.catalog()
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
                {
                    "name": "intake_knowledge_points",
                    "path": "workflow_nodes/example_intake.py",
                    "timeout_seconds": 600,
                },
                {
                    "name": "publish_content",
                    "path": "workflow_nodes/example_publish.py",
                    "timeout_seconds": 600,
                },
            ],
        }
    ]


def test_executor_catalog_does_not_expose_agent_runtimes(
    service: ExecutorCatalogService,
) -> None:
    result = service.catalog()
    executors_by_id = {executor["id"]: executor for executor in result["executors"]}

    assert "pi-video-main" not in executors_by_id
    assert "pi-default" not in executors_by_id
    assert "pi" not in executors_by_id
    assert set(executors_by_id) == {"code-default"}


def test_catalog_exposes_published_agent_definitions(
    service: ExecutorCatalogService,
) -> None:
    result = service.catalog()
    agents_by_id = {agent["id"]: agent for agent in result["agents"]}

    # conftest 播种的 published catalog（示例 workflow 的 4 个 agent）。
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
    service: ExecutorCatalogService, job_db, settings
) -> None:
    """Catalog reads the DB published rows: an admin edit shows up without restart."""
    definitions = ExecutorDefinitionService(job_db.path, settings.root_dir)
    edited = {
        "kind": "code",
        "global_capacity": 4,
        "capabilities": {"clean_and_parse": {"path": "workflow_nodes/question_clean_parse.py"}},
    }
    definitions.save_draft("code-default", edited, "user:admin")
    definitions.publish("code-default")
    # TRUNCATE isolation may leave a stale TTL entry from an earlier test.
    reset_published_executor_cache()

    result = service.catalog()

    executors_by_id = {executor["id"]: executor for executor in result["executors"]}
    assert executors_by_id["code-default"]["global_capacity"] == 4
    assert executors_by_id["code-default"]["capabilities"] == ["clean_and_parse"]
