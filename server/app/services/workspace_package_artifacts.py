from __future__ import annotations

from pathlib import Path

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

_VIDEO_KNOWLEDGE_KEY = "video_knowledge"


def workspace_artifact_names(
    root_dir: Path, workflow_keys: set[str], base_names: set[str]
) -> list[str]:
    """Return the curated list of artifact names for packaging.

    ``video_knowledge`` packages use a fixed deliverable list
    (``VIDEO_KNOWLEDGE_PACKAGE_FILES``); intermediate node outputs such as
    ``source.mp4`` or ``video_input.json`` are never packaged.

    For other workflows, ``base_names`` preserves existing question-job
    packaging behavior and each workflow's declared node outputs are merged in.
    """
    if workflow_keys and workflow_keys <= {_VIDEO_KNOWLEDGE_KEY}:
        return list(VIDEO_KNOWLEDGE_PACKAGE_FILES)
    names = set(base_names)
    for workflow_key in workflow_keys:
        if not workflow_key:
            continue
        try:
            definition = load_registered_workflow(root_dir, workflow_key)
        except (KeyError, FileNotFoundError):
            continue
        for node in definition.nodes.values():
            names.update(node.outputs)
    return sorted(names)
