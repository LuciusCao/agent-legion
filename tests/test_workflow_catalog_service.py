from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revision_format import workflow_definition_to_response_payload


def test_workflow_catalog_lists_registered_workflows(settings):
    service = WorkflowCatalogService(settings)
    result = service.list_workflows()
    assert result[0].keys() == {"key", "label", "description", "origin"}
    assert {entry["key"] for entry in result} == {
        "education_video_problems_generation",
        "question_comprehension_info",
        "video_knowledge",
    }
    assert all(entry["origin"] == "builtin" for entry in result)


def test_workflow_catalog_loads_workflow_definition(settings):
    service = WorkflowCatalogService(settings)
    payload = service.workflow("question_comprehension_info")
    assert payload["key"] == "question_comprehension_info"
    assert "nodes" in payload
    assert "intake" in payload


def test_workflow_catalog_payload_matches_revision_serializer(settings):
    service = WorkflowCatalogService(settings)
    payload = service.workflow("question_comprehension_info")
    definition = service.definition("question_comprehension_info")

    assert payload == workflow_definition_to_response_payload(definition)
    # Field-drift lock: catalog nodes expose terminal outcomes like revisions do.
    assert any(node["terminal"] for node in payload["nodes"])
