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
            "assemble_package",
            "clean_and_parse",
            "fetch_question_context",
            "fetch_questions",
        ],
    }
