import contextlib
from pathlib import Path

from server.app.pipelines.definition import PipelineDefinition, load_pipeline_definition

PIPELINE_FILES = {
    "question_content": "question_content.yaml",
    "reading_analysis": "reading_analysis.yaml",
}


def load_registered_pipeline(root_dir: Path, pipeline_key: str) -> PipelineDefinition:
    filename = PIPELINE_FILES.get(pipeline_key)
    if filename is None:
        raise KeyError(pipeline_key)
    return load_pipeline_definition(root_dir / "config" / "pipelines" / filename)


def list_registered_pipelines(root_dir: Path) -> list[PipelineDefinition]:
    pipelines: list[PipelineDefinition] = []
    for key in PIPELINE_FILES:
        with contextlib.suppress(KeyError, FileNotFoundError):
            pipelines.append(load_registered_pipeline(root_dir, key))
    return pipelines
