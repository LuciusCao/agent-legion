from server.app.workflows.builtin import BUILTIN_WORKFLOW_DEFINITIONS
from tests.helpers import load_builtin_definition


def test_video_knowledge_workflow_is_linear_knowledge_video_dag() -> None:
    definition = load_builtin_definition("video_knowledge")

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
    raw_nodes = BUILTIN_WORKFLOW_DEFINITIONS["video_knowledge"]["nodes"]
    assert all("runner" not in node for node in raw_nodes.values())
    assert all("agent" not in node for node in raw_nodes.values())
    definition = load_builtin_definition("video_knowledge")
    assert definition.nodes["download"].capability == "download_video"
    assert definition.nodes["transcribe"].capability == "transcribe_video"
    assert definition.nodes["assemble"].capability == "assemble_video_metadata"
    assert definition.nodes["package"].capability == "package_video_job"


def test_video_knowledge_download_node_outputs_video_input() -> None:
    definition = load_builtin_definition("video_knowledge")
    download = definition.nodes["download"]
    # The node resolves knowledge source_refs at execution time (CMS access is
    # configured on the node config, not a DAG resource) and writes the
    # resolved fields back to video_input.json.
    assert "video_input.json" in download.outputs
