from __future__ import annotations

import pytest

from server.app.executors.config import CodeExecutorConfig
from server.app.services.workflow_node_files import (
    NodeFileError,
    read_node_file,
    referencing_capabilities,
    resolve_node_path,
    workflow_nodes_dir,
    write_node_file,
)

VALID_CONTENT = "def run(job, job_dir, runtime):\n    return None\n"


def _nodes_dir(tmp_path):
    nodes_dir = tmp_path / "workflow_nodes"
    nodes_dir.mkdir()
    (nodes_dir / "demo_node.py").write_text(VALID_CONTENT, encoding="utf-8")
    return nodes_dir


def test_workflow_nodes_dir_defaults_to_repo_root() -> None:
    from server.app.services.workflow_node_files import REPO_ROOT

    assert workflow_nodes_dir() == REPO_ROOT / "workflow_nodes"
    assert workflow_nodes_dir().is_dir()


def test_resolve_accepts_plain_and_prefixed_names(tmp_path) -> None:
    nodes_dir = _nodes_dir(tmp_path)

    plain = resolve_node_path(nodes_dir, "demo_node.py")
    prefixed = resolve_node_path(nodes_dir, "workflow_nodes/demo_node.py")

    assert plain == prefixed == nodes_dir / "demo_node.py"


@pytest.mark.parametrize(
    "name",
    [
        "/etc/passwd",
        "../x.py",
        "sub/dir.py",
        "notes.txt",
        "__init__.py",
        "__pycache__.py",
        "",
    ],
)
def test_resolve_rejects_invalid_names(tmp_path, name) -> None:
    with pytest.raises(NodeFileError):
        resolve_node_path(_nodes_dir(tmp_path), name)


def test_resolve_missing_file_raises_not_found(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_node_path(_nodes_dir(tmp_path), "missing.py")


def test_read_node_file_returns_display_path_and_content(tmp_path) -> None:
    nodes_dir = _nodes_dir(tmp_path)

    path, content = read_node_file(nodes_dir, "demo_node.py")

    assert path == "workflow_nodes/demo_node.py"
    assert content == VALID_CONTENT


def test_write_node_file_validates_content(tmp_path) -> None:
    nodes_dir = _nodes_dir(tmp_path)

    with pytest.raises(NodeFileError, match="not valid Python"):
        write_node_file(nodes_dir, "demo_node.py", "def run(:\n")
    with pytest.raises(NodeFileError, match="module-level 'run'"):
        write_node_file(nodes_dir, "demo_node.py", "X = 1\n")

    assert (nodes_dir / "demo_node.py").read_text(encoding="utf-8") == VALID_CONTENT


def test_write_node_file_accepts_async_run(tmp_path) -> None:
    nodes_dir = _nodes_dir(tmp_path)
    updated = "async def run(job, job_dir, runtime):\n    return None\n"

    path = write_node_file(nodes_dir, "demo_node.py", updated)

    assert path == "workflow_nodes/demo_node.py"
    assert (nodes_dir / "demo_node.py").read_text(encoding="utf-8") == updated
    assert [p.name for p in nodes_dir.iterdir()] == ["demo_node.py"]


def test_referencing_capabilities_matches_code_paths() -> None:
    definitions = {
        "code-default": CodeExecutorConfig.model_validate(
            {
                "kind": "code",
                "global_capacity": 1,
                "capabilities": {
                    "fetch_questions": {"path": "workflow_nodes/question_intake.py"},
                    "download_video": {"path": "workflow_nodes/video_download.py"},
                },
            }
        ),
    }

    assert referencing_capabilities(definitions, "workflow_nodes/question_intake.py") == [
        {"executor_id": "code-default", "capability": "fetch_questions"}
    ]
    assert referencing_capabilities(definitions, "workflow_nodes/other.py") == []
