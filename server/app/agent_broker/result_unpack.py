"""Result-archive unpacking for Worker-completed executions.

Split out of ``agent_completion.py`` for the file-size budget: extraction is
bundle-domain code — it validates every promoted path against the job dir
(Worker archives are untrusted) and knows the batch-2 ``node.log`` contract.
The kind='code' result *metadata* keys — the other half of that result
contract — are declared once in ``shared.CODE_RESULT_METADATA_KEYS`` and read
Host-side by ``parse_result_metadata``
(server/app/routes/agent_worker_results.py); both mirrors are guarded by
tests/workers/test_protocol_sync.py (#282).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from server.app.agent_broker.agent_bundle import (
    CODE_RESULT_LOG_MEMBER,
    AgentBundleError,
    extract_agent_result,
)
from server.app.agent_broker.claim_paths import claim_log_path


def safe_relative_dir(value: str) -> PurePosixPath | None:
    if not value:
        return None
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    return relative


def code_result_log_target(manifest: dict[str, Any], data_dir: Path) -> Path | None:
    """Canonical on-disk log path for a kind='code' result's ``node.log``.

    Batch 2 (decision 10): the Worker ships the node's captured stdout/stderr
    as a fixed archive member; the Host lands it at the same
    ``data/logs/jobs/...`` path a local run would use (node_runs.log_path
    already points there from the claim insert). None for agent manifests or
    unmappable legacy paths.
    """
    if str(manifest.get("kind") or "") != "code":
        return None
    relative = safe_relative_dir(claim_log_path(manifest, data_dir))
    return data_dir / relative if relative is not None else None


def unpack_agent_result(
    archive_path: Path,
    job_dir: Path,
    expected: tuple[str, ...],
    run_dir: str = "",
    log_target: Path | None = None,
) -> None:
    """Extract into a staging dir, then promote declared expected outputs plus
    the Worker run dir's ``events.jsonl``.

    Worker archives are untrusted: nothing outside ``expected``, that single
    log file, and — for kind='code' results — the fixed ``node.log`` member
    lands on disk, so a Worker cannot clobber other nodes' inputs/outputs or
    plant files to spoof server-side decisions (log display and token parsing
    are read-only consumers)."""
    with tempfile.TemporaryDirectory(prefix=".result-staging-", dir=job_dir) as staging:
        staging_dir = Path(staging)
        extract_agent_result(archive_path, staging_dir)
        for name in expected:
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise AgentBundleError(f"unsafe expected output name: {name!r}")
            source = staging_dir / relative
            if source.is_file():
                target = job_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
        run_dir_relative = safe_relative_dir(run_dir)
        if run_dir_relative is not None:
            events_source = staging_dir / run_dir_relative / "events.jsonl"
            if events_source.is_file():
                events_target = job_dir / run_dir_relative / "events.jsonl"
                events_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(events_source), str(events_target))
        if log_target is not None:
            log_source = staging_dir / CODE_RESULT_LOG_MEMBER
            if log_source.is_file():
                log_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(log_source), str(log_target))
