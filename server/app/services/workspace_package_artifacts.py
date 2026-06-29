from __future__ import annotations

from pathlib import Path

from server.app.workflows.registry import load_registered_workflow


def workspace_artifact_names(
    root_dir: Path, workflow_keys: set[str], base_names: set[str]
) -> list[str]:
    """Return a workflow-neutral curated list of artifact names for packaging.

    ``base_names`` preserves existing question-job packaging behavior. Each
    workflow's declared node outputs are merged in, so workflows such as
    ``video_knowledge`` automatically include deliverables like ``source.mp4``,
    ``subtitles.srt``, ``metadata.json``, ``report.md``, ``upload_params.json``
    and ``package_manifest.json`` without branching on legacy phase names.
    """
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
