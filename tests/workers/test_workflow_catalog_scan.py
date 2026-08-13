"""Worker scan list backed by the DB workflow catalog."""

from __future__ import annotations

from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.workflow_worker.catalog_scan import (
    iter_scan_entries,
    load_workflow_scan_entries,
)


def test_scan_entries_split_definitions_and_definitionless_keys(settings) -> None:
    WorkflowCatalogService(settings).register("acme_quiz_flow", "Acme Quiz")

    definitions, definitionless_keys = load_workflow_scan_entries(settings)

    assert {definition.key for definition in definitions} == {
        "question_comprehension_info",
        "video_knowledge",
    }
    assert definitionless_keys == ["acme_quiz_flow"]
    entries = iter_scan_entries(definitions, definitionless_keys)
    assert entries[-1] == ("acme_quiz_flow", None)
