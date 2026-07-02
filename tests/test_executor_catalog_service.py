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
    }


def test_catalog_exposes_video_pi_agent_capabilities(service: ExecutorCatalogService) -> None:
    result = service.catalog()
    executors_by_id = {executor["id"]: executor for executor in result["executors"]}
    capabilities_by_executor = {
        executor["id"]: set(executor["capabilities"]) for executor in result["executors"]
    }

    assert "pi-video-main" not in executors_by_id
    assert "pi-default" not in executors_by_id
    assert executors_by_id["pi"]["kind"] == "pi"
    assert {
        "generate_key_info",
        "review_key_info",
        "generate_possible_errors",
        "review_possible_errors",
        "assess_comprehension_difficulty",
        "review_subtitles",
        "generate_chapters",
        "generate_interactions",
        "review_video_content",
    }.issubset(capabilities_by_executor["pi"])
