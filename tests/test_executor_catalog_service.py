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
                "assemble_comprehension_info",
                "assemble_video_metadata",
                "classify_comprehension_eligibility",
                "clean_and_parse",
                "download_video",
                "fetch_questions",
                "finalize_non_uploadable",
                "intake_knowledge_points",
                "package_video_job",
                "publish_content",
                "transcribe_video",
            ],
            "capability_details": [
                {
                    "name": "assemble_comprehension_info",
                    "path": "workflow_nodes/comprehension_assemble.py",
                    "timeout_seconds": 600,
                },
                {
                    "name": "assemble_video_metadata",
                    "path": "workflow_nodes/video_assemble.py",
                    "timeout_seconds": 600,
                },
                {
                    "name": "classify_comprehension_eligibility",
                    "path": "workflow_nodes/comprehension_classify.py",
                    "timeout_seconds": 600,
                },
                {
                    "name": "clean_and_parse",
                    "path": "workflow_nodes/question_clean_parse.py",
                    "timeout_seconds": 600,
                },
                {
                    "name": "download_video",
                    "path": "workflow_nodes/video_download.py",
                    "timeout_seconds": 600,
                },
                {
                    "name": "fetch_questions",
                    "path": "workflow_nodes/question_intake.py",
                    "timeout_seconds": 600,
                },
                {
                    "name": "finalize_non_uploadable",
                    "path": "workflow_nodes/comprehension_finalize.py",
                    "timeout_seconds": 600,
                },
                {
                    "name": "intake_knowledge_points",
                    "path": "workflow_nodes/example_intake.py",
                    "timeout_seconds": 600,
                },
                {
                    "name": "package_video_job",
                    "path": "workflow_nodes/video_package.py",
                    "timeout_seconds": 600,
                },
                {
                    "name": "publish_content",
                    "path": "workflow_nodes/example_publish.py",
                    "timeout_seconds": 600,
                },
                {
                    "name": "transcribe_video",
                    "path": "workflow_nodes/video_transcribe.py",
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

    # conftest 播种的 published catalog（schema v27：video agent 已翻转 velites）。
    agent = agents_by_id["video-content-review-v1"]
    assert agent["runtime"] == "velites"
    assert agent["capability"] == "review_video_content"
    assert agent["skill"] == "video_knowledge/review_video_content"
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
