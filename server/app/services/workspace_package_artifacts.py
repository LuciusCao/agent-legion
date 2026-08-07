from __future__ import annotations

from server.app.workflows.registry import load_registered_workflow

VIDEO_KNOWLEDGE_PACKAGE_FILES = [
    "chapters.json",
    "interactions.json",
    "subtitles.srt",
    "transcription.json",
    "metadata.json",
    "upload_params.json",
    "package_manifest.json",
]


def workspace_artifact_names(workflow_keys: set[str], base_names: set[str]) -> list[str]:
    """Return the curated artifact list for packaging.

    ``video_knowledge`` packages use the fixed ``VIDEO_KNOWLEDGE_PACKAGE_FILES``
    list; other workflows merge ``base_names`` with declared node outputs.
    """
    if workflow_keys and workflow_keys <= {"video_knowledge"}:
        return list(VIDEO_KNOWLEDGE_PACKAGE_FILES)
    names = set(base_names)
    for workflow_key in workflow_keys:
        if not workflow_key:
            continue
        try:
            definition = load_registered_workflow(workflow_key)
        except KeyError:
            continue
        for node in definition.nodes.values():
            names.update(node.outputs)
    return sorted(names)
