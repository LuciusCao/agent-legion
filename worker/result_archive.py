"""Result-archive assembly for kind='code' executions (#282 split).

``prepare_code_result`` lived in ``worker/code_runner.py`` until the file
outgrew its budget; it moved here because it is a pure assembly step over
the finished execution (metadata dict + tar.gz archive), not part of the
run loop — the only caller is ``worker/upload/prepare.py``'s
``prepare_or_failed``.
"""

from __future__ import annotations

import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

from shared.code_contract import CODE_RESULT_LOG_MEMBER, CODE_RESULT_METADATA_KEYS
from worker.upload.queue import UploadTask


def prepare_code_result(task: UploadTask) -> tuple[dict[str, Any], Path, list[str]]:
    """Build (metadata, archive, output names) for a kind='code' result.

    Archive contract (mirrors the Host-side reader
    ``server/app/agent_broker/result_unpack.py`` — keep in sync): expected
    outputs at their job-dir-relative names plus ``node.log`` at the archive
    root; no events.jsonl/run_dir. The captured node.log ships even for
    cancelled runs (batch 2 decision 10). The metadata *keys* are the
    cross-process contract ``shared.CODE_RESULT_METADATA_KEYS`` (#282): the
    Host consumes them in ``parse_result_metadata``
    (server/app/routes/agent_worker_results.py), defaulting absent optional
    keys."""
    archive = task.execution_dir / "result.tar.gz"
    job_dir = task.execution_dir / "job"
    outcome = task.code_result or {}
    outputs = [name for name in task.expected_outputs if (job_dir / PurePosixPath(name)).is_file()]
    metadata: dict[str, Any] = {
        "status": str(outcome.get("status") or "failed"),
        "exit_code": task.exit_code,
        "error_message": str(outcome.get("error_message") or ""),
        "command": list(task.command),
        "output_artifacts": {},
    }
    auth_failure = str(outcome.get("auth_failure_connection") or "").strip()
    if auth_failure:
        metadata["auth_failure_connection"] = auth_failure
    # #282 键集守卫：metadata 键是 Worker↔Host 的进程边界契约（无编译器、无
    # schema），写出的键必须落在 shared.CODE_RESULT_METADATA_KEYS 之内且必含
    # 恒在键——auth_failure_connection 仅在节点实际上报时携带。漂移在此
    # fail-closed（prepare_or_failed 会降级为 failed 上报，不会静默丢字段）；
    # 契约回归另由 tests/workers/test_protocol_sync.py 的守卫测试拦截。
    written = set(metadata)
    if written - CODE_RESULT_METADATA_KEYS or (
        CODE_RESULT_METADATA_KEYS - {"auth_failure_connection"} - written
    ):
        raise ValueError(
            "code result metadata key set does not match "
            f"shared.CODE_RESULT_METADATA_KEYS: {sorted(written)}"
        )
    # #160 D12：直传判定与 upload_queue._bulk_transfer 一致（#201 收敛进
    # UploadTask.is_direct_upload）；直传时产物不内嵌归档（字节走 presigned
    # PUT），node.log 照常携带。
    direct = task.is_direct_upload(outputs)
    with tarfile.open(archive, "w:gz") as tar:
        if not direct:
            for name in outputs:
                tar.add(job_dir / PurePosixPath(name), arcname=name)
        node_log = task.execution_dir / CODE_RESULT_LOG_MEMBER
        if node_log.is_file():
            tar.add(node_log, arcname=CODE_RESULT_LOG_MEMBER)
    return metadata, archive, outputs
