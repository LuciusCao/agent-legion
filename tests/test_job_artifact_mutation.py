import shutil

import pytest

from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode


@pytest.fixture
def mutation_service(tmp_path):
    return JobArtifactMutationService(tmp_path)


@pytest.fixture
def definition():
    return WorkflowDefinition(
        key="test_workflow",
        label="Test Workflow",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="a", outputs=["a.json"]),
            "b": WorkflowNode(key="b", label="B", capability="b", after=["a"], outputs=["b.json"]),
            "c": WorkflowNode(key="c", label="C", capability="c", after=["b"], outputs=["c.json"]),
        },
    )


def test_stage_outputs_moves_selected_and_descendant_outputs(
    tmp_path, mutation_service, definition
):
    storage_dir = tmp_path / "job"
    storage_dir.mkdir()
    (storage_dir / "a.json").write_text("a")
    (storage_dir / "b.json").write_text("b")
    (storage_dir / "c.json").write_text("c")

    job = {"storage_dir": str(storage_dir)}
    staged = mutation_service.stage_outputs(job, ["b"], definition)

    assert not (storage_dir / "b.json").exists()
    assert not (storage_dir / "c.json").exists()
    assert (storage_dir / "a.json").exists()
    assert (storage_dir / ".staged" / "b.json").exists()
    assert (storage_dir / ".staged" / "c.json").exists()

    staged.commit()
    assert not (storage_dir / ".staged" / "b.json").exists()
    assert not (storage_dir / ".staged" / "c.json").exists()


def test_stage_outputs_rollback_restores_files(tmp_path, mutation_service, definition):
    storage_dir = tmp_path / "job"
    storage_dir.mkdir()
    (storage_dir / "a.json").write_text("a")
    (storage_dir / "b.json").write_text("b")

    job = {"storage_dir": str(storage_dir)}
    staged = mutation_service.stage_outputs(job, ["a"], definition)

    assert not (storage_dir / "a.json").exists()
    staged.rollback()
    assert (storage_dir / "a.json").read_text() == "a"
    assert (storage_dir / "b.json").exists()
    assert not (storage_dir / ".staged").exists()


def test_stage_outputs_ignores_missing_files(tmp_path, mutation_service, definition):
    storage_dir = tmp_path / "job"
    storage_dir.mkdir()
    (storage_dir / "a.json").write_text("a")

    job = {"storage_dir": str(storage_dir)}
    staged = mutation_service.stage_outputs(job, ["b"], definition)

    assert (storage_dir / "a.json").exists()
    staged.commit()
    assert not (storage_dir / ".staged").exists()


def test_stage_outputs_preserves_inputs_and_unrelated_files(tmp_path, mutation_service, definition):
    storage_dir = tmp_path / "job"
    storage_dir.mkdir()
    (storage_dir / "a.json").write_text("a")
    (storage_dir / "b.json").write_text("b")
    (storage_dir / "c.json").write_text("c")
    (storage_dir / "extra.log").write_text("log")

    job = {"storage_dir": str(storage_dir)}
    staged = mutation_service.stage_outputs(job, ["b"], definition)
    staged.commit()

    assert (storage_dir / "a.json").exists()
    assert (storage_dir / "extra.log").exists()
    assert not (storage_dir / "b.json").exists()
    assert not (storage_dir / "c.json").exists()


def test_stage_outputs_rejects_escape_paths(tmp_path, mutation_service):
    definition = WorkflowDefinition(
        key="test_workflow",
        label="Test Workflow",
        intake=WorkflowIntake(),
        nodes={
            "a": WorkflowNode(key="a", label="A", capability="a", outputs=["../escape.json"]),
        },
    )
    storage_dir = tmp_path / "job"
    storage_dir.mkdir()

    job = {"storage_dir": str(storage_dir)}
    with pytest.raises(ValueError, match="escapes"):
        mutation_service.stage_outputs(job, ["a"], definition)


def test_stage_outputs_idempotent_commit_and_rollback(tmp_path, mutation_service, definition):
    storage_dir = tmp_path / "job"
    storage_dir.mkdir()
    (storage_dir / "a.json").write_text("a")

    job = {"storage_dir": str(storage_dir)}
    staged = mutation_service.stage_outputs(job, ["a"], definition)
    staged.commit()
    staged.commit()
    staged.rollback()  # no-op after commit
    assert not (storage_dir / ".staged" / "a.json").exists()


def test_stage_outputs_restores_partial_moves_when_later_move_fails(
    tmp_path, mutation_service, definition, monkeypatch
):
    storage_dir = tmp_path / "job"
    storage_dir.mkdir()
    (storage_dir / "a.json").write_text("a")
    (storage_dir / "b.json").write_text("b")

    original_move = shutil.move
    calls = 0

    def flaky_move(src, dst):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk failure")
        return original_move(src, dst)

    monkeypatch.setattr("server.app.services.job_artifact_mutation.shutil.move", flaky_move)

    with pytest.raises(OSError, match="disk failure"):
        mutation_service.stage_outputs({"storage_dir": str(storage_dir)}, ["a"], definition)

    assert (storage_dir / "a.json").read_text() == "a"
    assert (storage_dir / "b.json").read_text() == "b"
    assert not (storage_dir / ".staged").exists()
