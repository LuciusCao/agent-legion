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


def test_catalog_exposes_video_pi_agent_capabilities(service: ExecutorCatalogService) -> None:
    result = service.catalog()
    executors_by_id = {executor["id"]: executor for executor in result["executors"]}
    capabilities_by_executor = {
        executor["id"]: set(executor["capabilities"]) for executor in result["executors"]
    }

    assert "pi-video-main" not in executors_by_id
    assert "pi-default" not in executors_by_id
    assert executors_by_id["pi"]["kind"] == "pi"
    details_by_name = {
        detail["name"]: detail for detail in executors_by_id["pi"]["capability_details"]
    }
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
    assert details_by_name["generate_key_info"] == {
        "name": "generate_key_info",
        "skill": "question_comprehension_info/generate_key_info",
        "tools": ["read", "write", "bash"],
        "skill_ref": "v1.3.8",
        "skill_commit": "5c5eae72064abde37bfc4b07a4b2f7e9637c473d",
        "provider": "deepseek",
        "model": "your-model-b",
        "thinking": "low",
    }
