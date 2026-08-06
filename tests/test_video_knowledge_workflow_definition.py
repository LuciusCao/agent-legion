from pathlib import Path

from server.app.workflows.definition import load_workflow_definition

ROOT = Path(__file__).resolve().parents[1]


def test_video_knowledge_workflow_is_linear_knowledge_video_dag() -> None:
    definition = load_workflow_definition(ROOT / "config/workflows/video_knowledge.yaml")

    assert definition.key == "video_knowledge"
    assert list(definition.nodes) == [
        "download",
        "transcribe",
        "subtitle_review",
        "chapter_generate",
        "interaction_generate",
        "content_review",
        "assemble",
        "package",
    ]
    assert definition.nodes["download"].after == []
    assert definition.nodes["transcribe"].after == ["download"]
    assert definition.nodes["package"].after == ["assemble"]
    assert definition.terminal_nodes == ["package"]


def test_video_knowledge_workflow_declares_capabilities_only() -> None:
    text = (ROOT / "config/workflows/video_knowledge.yaml").read_text(encoding="utf-8")
    assert "runner:" not in text
    assert "agent:" not in text
    definition = load_workflow_definition(ROOT / "config/workflows/video_knowledge.yaml")
    assert definition.nodes["download"].capability == "download_video"
    assert definition.nodes["transcribe"].capability == "transcribe_video"
    assert definition.nodes["assemble"].capability == "assemble_video_metadata"
    assert definition.nodes["package"].capability == "package_video_job"


def test_video_knowledge_download_node_outputs_video_input() -> None:
    definition = load_workflow_definition(ROOT / "config/workflows/video_knowledge.yaml")
    download = definition.nodes["download"]
    # The node resolves knowledge source_refs at execution time (CMS access is
    # configured on the node config, not a DAG resource) and writes the
    # resolved fields back to video_input.json.
    assert "video_input.json" in download.outputs
