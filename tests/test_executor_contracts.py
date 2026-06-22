from pathlib import Path

from server.app.executors.models import ExecutionContext, ExecutionResult


def test_execution_context_carries_persisted_identity(tmp_path: Path) -> None:
    context = ExecutionContext(
        execution_id="exec-1",
        lease_id="lease-1",
        node_run_id=7,
        executor_id="pi-default",
        workspace_id="ws-a",
        job_id="job-1",
        workflow_key="question_comprehension_info",
        node_key="review_keywords",
        capability="review_keywords",
        workspace={"id": "ws-a", "name": "Workspace A"},
        job={
            "id": "job-1",
            "workspace_id": "ws-a",
            "workflow_key": "question_comprehension_info",
            "source_type": "question",
            "source_id": "q-1",
            "batch_id": "",
            "title": "Question 1",
            "storage_dir": str(tmp_path),
            "stem": "",
        },
        job_dir=tmp_path,
        log_path=tmp_path / "run.log",
        inputs=("keywords.json",),
        expected_outputs=("review.json",),
    )
    assert context.executor_id == "pi-default"
    assert context.node_run_id == 7


def test_execution_result_is_executor_neutral() -> None:
    result = ExecutionResult(
        status="completed",
        exit_code=0,
        command=("pi", "prompt.md"),
        produced_artifacts=("review.json",),
    )
    assert result.status == "completed"
    assert result.command == ("pi", "prompt.md")
