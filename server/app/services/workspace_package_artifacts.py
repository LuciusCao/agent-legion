from __future__ import annotations

from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.settings import Settings

VIDEO_KNOWLEDGE_PACKAGE_FILES = [
    "chapters.json",
    "interactions.json",
    "subtitles.srt",
    "transcription.json",
    "metadata.json",
    "upload_params.json",
    "package_manifest.json",
]


def workspace_artifact_names(
    settings: Settings, workflow_keys: set[str], base_names: set[str]
) -> list[str]:
    """Return the curated artifact list for packaging.

    ``video_knowledge`` packages use the fixed ``VIDEO_KNOWLEDGE_PACKAGE_FILES``
    list; other workflows merge ``base_names`` with declared node outputs from
    the DB-backed workflow catalog.
    """
    if workflow_keys and workflow_keys <= {"video_knowledge"}:
        return list(VIDEO_KNOWLEDGE_PACKAGE_FILES)
    catalog = WorkflowCatalogService(settings)
    names = set(base_names)
    for workflow_key in workflow_keys:
        if not workflow_key:
            continue
        definition = catalog.definition_or_none(workflow_key)
        if definition is None:
            continue
        for node in definition.nodes.values():
            names.update(node.outputs)
    return sorted(names)
