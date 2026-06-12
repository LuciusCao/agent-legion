from server.app.services.pipeline_catalog import PipelineCatalogService


def test_pipeline_catalog_lists_registered_pipelines(settings):
    service = PipelineCatalogService(settings)
    result = service.list_pipelines()
    assert result[0].keys() == {"key", "label", "concurrency"}
    assert result[0]["concurrency"].keys() == {"local", "agent", "nodes"}


def test_pipeline_catalog_loads_pipeline_definition(settings):
    service = PipelineCatalogService(settings)
    payload = service.pipeline("question_content")
    assert payload["key"] == "question_content"
    assert "nodes" in payload
    assert "intake" in payload
