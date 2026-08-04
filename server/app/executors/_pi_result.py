import logging

from server.app.executors._shard_contract import read_shard_output
from server.app.executors.models import ExecutionContext, ExecutionResult
from server.app.workflows.pi_runner import PiRunResult

logger = logging.getLogger(__name__)


def to_execution_result(result: PiRunResult, context: ExecutionContext) -> ExecutionResult:
    """Map a Pi runner result to the executor-neutral result contract."""
    run_dir = str(result.run_dir)
    session_dir = str(result.session_dir)
    if result.status == "completed" and (not run_dir or not session_dir):
        logger.warning(
            "Pi run completed without run_dir/session_dir for %s.%s: run_dir=%r session_dir=%r",
            context.job_id,
            context.node_key,
            run_dir,
            session_dir,
        )
    return ExecutionResult(
        status=result.status,
        exit_code=result.exit_code,
        error_message=result.error_message,
        command=tuple(result.command),
        log_path=str(context.log_path),
        run_dir=run_dir,
        session_dir=session_dir,
        skill_version=result.skill_version,
        produced_artifacts=tuple(
            name for name in context.expected_outputs if (context.job_dir / name).is_file()
        ),
        output_json=(
            read_shard_output(context.job_dir, context.runtime)
            if result.status == "completed"
            else ""
        ),
    )
