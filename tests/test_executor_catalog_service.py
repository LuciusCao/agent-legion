import pytest

from server.app.services.executor_catalog import ExecutorCatalogService


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
                "package_video_job",
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
                    "name": "package_video_job",
                    "path": "workflow_nodes/video_package.py",
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


def test_catalog_exposes_agent_definitions_with_runtime_defaults(
    service: ExecutorCatalogService,
) -> None:
    result = service.catalog()
    agents_by_id = {agent["id"]: agent for agent in result["agents"]}

    agent = agents_by_id["video-content-review-v1"]
    assert agent["runtime"] == "pi"
    assert agent["capability"] == "review_video_content"
    assert agent["skill"] == "video_knowledge/review_video_content"
    assert agent["tools"] == ["read", "write", "bash"]
    assert agent["provider"] == "gateway"
    # yaml 默认 model 已清空（issue #13）：占位符 model 在 enqueue 被拒。
    assert agent["model"] == ""
    assert agent["thinking"] == "low"
