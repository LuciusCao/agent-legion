from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.workflows.pi_runner import PiRunResult


def to_execution_result(result: PiRunResult, context: ExecutionContext) -> ExecutionResult:
    """Map a Pi runner result to the executor-neutral result contract."""
    return ExecutionResult(
        status=result.status,
        exit_code=result.exit_code,
        error_message=result.error_message,
        command=tuple(result.command),
        log_path=str(context.log_path),
        run_dir=str(result.run_dir),
        session_dir=str(result.session_dir),
        produced_artifacts=tuple(
            name for name in context.expected_outputs if (context.job_dir / name).is_file()
        ),
    )
