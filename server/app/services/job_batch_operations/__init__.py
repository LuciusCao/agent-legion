"""Batch job operations async queueing (#946).

Accept side (``service``): threshold dispatch + the frozen-spec row write.
Execution side (``feeder`` + ``handlers``): the drip-feed loop that replays
the sync per-job semantics through the same service entry points. Design:
docs/architecture/batch-ops-async.md.
"""

from server.app.services.job_batch_operations.feeder import (
    JOB_BATCH_OPS_CHUNK_SIZE,
    JobBatchOpsFeeder,
)
from server.app.services.job_batch_operations.handlers import BatchOpsExecutors
from server.app.services.job_batch_operations.retry import (
    RETRYABLE_KINDS,
    submit_retry_failed,
)
from server.app.services.job_batch_operations.service import (
    BATCH_OPS_ASYNC_THRESHOLD,
    DEFAULT_BATCH_OPS_ASYNC_THRESHOLD,
    JOB_BATCH_OPERATION_KINDS,
    dispatch_batch_selection,
    dispatch_rerun_by_failure,
    get_operation_for_workspace,
    operation_record,
    should_enqueue,
    submit_operation,
    submit_operation_if_large,
)

__all__ = [
    "BATCH_OPS_ASYNC_THRESHOLD",
    "DEFAULT_BATCH_OPS_ASYNC_THRESHOLD",
    "JOB_BATCH_OPS_CHUNK_SIZE",
    "JOB_BATCH_OPERATION_KINDS",
    "RETRYABLE_KINDS",
    "BatchOpsExecutors",
    "JobBatchOpsFeeder",
    "dispatch_batch_selection",
    "dispatch_rerun_by_failure",
    "get_operation_for_workspace",
    "operation_record",
    "should_enqueue",
    "submit_operation",
    "submit_operation_if_large",
    "submit_retry_failed",
]
