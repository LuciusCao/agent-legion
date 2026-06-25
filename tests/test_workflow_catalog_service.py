from server.app.services.workflow_catalog import WorkflowCatalogService


def test_workflow_catalog_lists_registered_workflows(settings):
    service = WorkflowCatalogService(settings)
    result = service.list_workflows()
    assert result[0].keys() == {"key", "label"}


def test_workflow_catalog_loads_workflow_definition(settings):
    service = WorkflowCatalogService(settings)
    payload = service.workflow("question_comprehension_info")
    assert payload["key"] == "question_comprehension_info"
    assert "nodes" in payload
    assert "intake" in payload
