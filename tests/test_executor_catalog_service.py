import pytest

from server.app.services.executor_catalog import ExecutorCatalogService


@pytest.fixture
def service(job_db, settings, agent_manager):
    return ExecutorCatalogService(settings)


def test_catalog_exposes_normalized_yaml_definitions(service: ExecutorCatalogService) -> None:
    result = service.catalog()
    assert result["executors"][0] == {
        "id": "local-default",
        "kind": "local",
        "global_capacity": 128,
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
                "handler": "question_comprehension_info.assemble_comprehension_info",
            },
            {
                "name": "assemble_video_metadata",
                "handler": "video_knowledge.assemble_video_metadata",
            },
            {
                "name": "classify_comprehension_eligibility",
                "handler": "question_comprehension_info.classify_comprehension_eligibility",
            },
            {
                "name": "clean_and_parse",
                "handler": "question_comprehension_info.clean_and_parse",
            },
            {"name": "download_video", "handler": "video_knowledge.download_video"},
            {
                "name": "fetch_questions",
                "handler": "question_comprehension_info.fetch_questions",
            },
            {
                "name": "finalize_non_uploadable",
                "handler": "question_comprehension_info.finalize_non_uploadable",
            },
            {"name": "package_video_job", "handler": "video_knowledge.package_video_job"},
            {"name": "transcribe_video", "handler": "video_knowledge.transcribe_video"},
        ],
    }


def test_executor_catalog_does_not_expose_agent_runtimes(
    service: ExecutorCatalogService,
) -> None:
    result = service.catalog()
    executors_by_id = {executor["id"]: executor for executor in result["executors"]}

    assert "pi-video-main" not in executors_by_id
    assert "pi-default" not in executors_by_id
    assert "pi" not in executors_by_id
    assert set(executors_by_id) == {"local-default"}


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
