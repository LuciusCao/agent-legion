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
from server.app.storage_paths import ensure_dir_once
from shared.code_contract import RESULT_METADATA_MEMBER, RESULT_OUTPUT_ARTIFACTS_MEMBER
from shared.stderr_tail import AGENT_STDERR_FILENAME, STDERR_TAIL_BYTES

# #843 评审 P1：不进提升面的协议成员（result.json = v2 元数据、
# result-output-artifacts.json = v1 换轨清单）——expected 命中即拒绝提升；
# node.log 走 completion_preflight 的既有保留源守卫（见 docstring）。
_NON_PROMOTABLE_MEMBERS = frozenset({RESULT_METADATA_MEMBER, RESULT_OUTPUT_ARTIFACTS_MEMBER})


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


def plan_agent_result_moves(
    staging_dir: Path,
    job_dir: Path,
    expected: tuple[str, ...],
    run_dir: str = "",
    log_target: Path | None = None,
) -> tuple[list[tuple[Path, Path]], tuple[str, ...]]:
    """Plan the (target, source) promotions out of an extracted staging dir.

    Returns the absolute-path move list plus the produced expected-output
    names; nothing is moved here — the caller decides where the promotion
    happens (``unpack_agent_result`` moves immediately; the completion path
    defers it into the lease-finish generation gate, #759 review P1-1).
    Worker archives are untrusted: nothing outside ``expected``, the run
    dir's ``events.jsonl`` and size-capped ``agent-stderr.log`` (#748), and
    — for kind='code' results — the fixed ``node.log`` member may land on
    disk, so a Worker cannot clobber other nodes' inputs/outputs
    or plant files to spoof server-side decisions (log display and token
    parsing are read-only consumers). The reserved
    ``result-output-artifacts.json`` member (#755 codex P1, the overflow
    fallback's direct-ref manifest) is therefore never promoted here — its
    only reader is the commit layer (result_output_manifest.py). #843 评审
    P1（纵深防御）：``result.json`` / ``result-output-artifacts.json`` 是
    不进提升面的协议成员（元数据 / v1 换轨清单——expected 命中它们的
    静默提升此前完全无守卫），此处直接拒绝：AgentBundleError →
    completion 的解包宽捕获 → 本节点诚实判败，绝不把归档成员（元数据）
    静默提升成产物。``node.log`` 的期望名冲突**不在本臂**——它走既有
    completion_preflight 的保留源守卫（#759 P2-B：失败路径仍落观测 log
    move，语义已被测试钉住）；入队守卫（manifest_guard）在源头把三个
    保留名一并拒绝。保留名比对按 ``PurePosixPath`` 归一化形态（#1164
    收口）：``./result.json`` 归一化即 ``result.json``、与 source 落点
    同路径，原字符串精确比对会放行该别名拼写；嵌套名（``sub/result.json``）
    归一化后仍是独立路径，不受影响。大小写变体（``RESULT.JSON``）不拦
    ——大小写敏感文件系统（生产 Linux）上是真不同路径；dev 的 macOS
    APFS 大小写不敏感形态不在守卫范围。
    """
    moves: list[tuple[Path, Path]] = []
    produced: list[str] = []
    for name in expected:
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise AgentBundleError(f"unsafe expected output name: {name!r}")
        # #1164 收口：归一化形态比对——``./result.json`` 归一化即保留成员，
        # 与下面的 source/job_dir join 是同一落点；原字符串精确比对会放行
        # 该别名拼写并把 staging 的元数据成员提升成产物。嵌套名
        # （``sub/result.json``）归一化后仍是独立路径，不受影响。
        if relative.as_posix() in _NON_PROMOTABLE_MEMBERS:
            raise AgentBundleError(
                f"expected output name {name!r} is reserved for the result archive"
            )
        source = staging_dir / relative
        if source.is_file():
            moves.append((job_dir / relative, source))
            produced.append(name)
    run_dir_relative = safe_relative_dir(run_dir)
    if run_dir_relative is not None:
        events_source = staging_dir / run_dir_relative / "events.jsonl"
        if events_source.is_file():
            moves.append((job_dir / run_dir_relative / "events.jsonl", events_source))
        # #748: the redacted crash-evidence tail lands beside events.jsonl
        # (the job dir's retained run dir); a member larger than any tail a
        # Worker writes is not evidence and is left in staging.
        stderr_source = staging_dir / run_dir_relative / AGENT_STDERR_FILENAME
        if stderr_source.is_file() and stderr_source.stat().st_size <= STDERR_TAIL_BYTES:
            moves.append((job_dir / run_dir_relative / AGENT_STDERR_FILENAME, stderr_source))
    if log_target is not None:
        log_source = staging_dir / CODE_RESULT_LOG_MEMBER
        if log_source.is_file():
            # #618: code results land node.log in the shared logs/jobs dir
            # (the claim insert already points node_runs there).
            moves.append((log_target, log_source))
    return moves, tuple(produced)


def unpack_agent_result(
    archive_path: Path,
    job_dir: Path,
    expected: tuple[str, ...],
    run_dir: str = "",
    log_target: Path | None = None,
) -> None:
    """Extract into a staging dir, then promote declared expected outputs plus
    the Worker run dir's ``events.jsonl`` and ``agent-stderr.log``.

    Immediate-promotion variant for tests and other non-gated callers; the
    completion path instead extracts with ``extract_agent_result`` and
    defers the promotion via ``plan_agent_result_moves`` into the
    lease-finish generation gate."""
    with tempfile.TemporaryDirectory(prefix=".result-staging-", dir=job_dir) as staging:
        staging_dir = Path(staging)
        extract_agent_result(archive_path, staging_dir)
        moves, _produced = plan_agent_result_moves(
            staging_dir, job_dir, expected, run_dir, log_target
        )
        for target, source in moves:
            ensure_dir_once(target.parent)
            shutil.move(str(source), str(target))
