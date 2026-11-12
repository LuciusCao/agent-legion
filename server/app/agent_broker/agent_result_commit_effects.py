"""Post-commit effects and unconditional cleanup for Agent Worker results."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.agent_broker import worker_events
from server.app.agent_broker.result_timing import ResultStageTimer, report_result_stages
from server.app.agent_control.completion import AgentOutcome, report_auth_failure_safe

if TYPE_CHECKING:
    from server.app.agent_broker import AgentExecutionBroker


def note_committed_result(
    broker: AgentExecutionBroker,
    execution_id: str,
    worker_id: str,
    payload: dict[str, Any],
    outcome: AgentOutcome,
) -> None:
    """Publish the terminal event and execute-done gauge after both commits."""
    # #490 execution.finished: outcome + wall time (claim → committed
    # result spans download/run/upload); claimed_at is read post-done,
    # so the wall time is best-effort.
    worker_events.note_execution_finished(
        execution_id, worker_id, payload, outcome, broker.database_dsn
    )
    # Runtime profile (#359): execute-stage done rate (a worker execution
    # reached its terminal state through the normal result path).
    from server.app.services.runtime_profile import profile

    profile.note_execution_done()
    if outcome.auth_failure_connection:
        # Batch 2 (design §5.3): the node recorded an upstream auth
        # failure; the Host performs the privileged invalidation.
        report_auth_failure_safe(broker.database_dsn, outcome.auth_failure_connection)


def cleanup_result_commit(
    broker: AgentExecutionBroker,
    stage_timer: ResultStageTimer,
    *,
    execution_id: str,
    worker_id: str,
    committed: bool,
    archive_name: str,
    payload: dict[str, Any],
) -> None:
    """Report every attempt, reclaim its archive, and retire committed bundles."""
    report_result_stages(
        stage_timer, execution_id=execution_id, worker_id=worker_id, committed=committed
    )
    # The archive name is unique to this attempt — always reclaim it.
    broker.discard_result_archive(archive_name)
    if committed:
        # Only a fully committed result retires the shared execution
        # bundle. On 409/500 paths the bundle must survive for
        # re-queued attempts; terminal-request bundles are reaped by
        # the sweeper (AgentExecutionBroker.reap_terminal_bundles).
        broker.retire_bundle(str(payload["manifest"].get("bundle_name", "")))
