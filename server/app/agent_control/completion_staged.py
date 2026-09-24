"""Staged completion tail for Worker results (#759 review P1-1).

Split out of ``completion.py`` for the file-size budget: once the result
archive is extracted into a staging dir, this module owns everything from the
landing preflight through the gated finish — the Worker-direct refs
verification, the unified read view (hardlinked remote downloads + staged
archive outputs; the link mechanics live in ``completion_view``), the
Host-side validation, the lease-armed artifact mirror, and the final
``leases.finish`` whose ``staged_file_moves`` ride the generation gate.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.agent_broker.remote_artifact_promote import discard_staging_refs
from server.app.agent_broker.remote_artifacts import apply_worker_artifact_refs
from server.app.agent_broker.result_timing import mark as mark_result_stage
from server.app.agent_broker.result_unpack import safe_relative_dir
from server.app.agent_control.completion_moves import gate_safe_staged_moves
from server.app.agent_control.completion_preflight import find_landing_conflict
from server.app.agent_control.completion_view import link_into_view
from server.app.executors._shard_contract import read_shard_output
from server.app.executors.artifact_mirror import upload_produced_artifacts
from server.app.executors.models import ExecutionResult
from server.app.workflows.worker_output_validation import validate_worker_outputs

if TYPE_CHECKING:
    from server.app.agent_broker.result_timing import ResultStageTimer
    from server.app.agent_control.completion import AgentCompletionHandler, AgentOutcome


def finish_staged(
    handler: AgentCompletionHandler,
    *,
    lease_id: str,
    worker_id: str,
    job_id: str,
    node_key: str,
    job: Any,
    manifest: dict,
    outcome: AgentOutcome,
    job_dir: Path,
    view_dir: Path,
    expected: tuple[str, ...],
    staged_moves: list[tuple[Path, Path]],
    cancelled: bool,
    stage_timer: ResultStageTimer | None,
) -> bool:
    """Completion tail after the archive is staged: verify refs, validate
    against the read view, mirror, then finish with the gated promotion."""
    # #759 对抗复审 P2 族：任何字节移动之前先做路径形态预检——两个通道
    # （归档暂存提升 / remote ref 落盘）各自宣称的形状若单文件系统不可能
    # 同时成立（前缀相撞），或落点祖先被现场非目录挡住，继续 apply 只会
    # 在 remote promote 已提交之后炸穿结果提交（codex #774 P2）。预检失
    # 败 = 干净 failed：零字节应用，staging key 保留；闸安全且未参与冲
    # 突的归档 moves（node.log 等）照常随失败 finish 落盘——冲突 move
    # 不挂，其 staging source 不再被抢先消耗，同名观测 move 随之能真正
    # 落盘而不是被误当重放跳过（codex #774 P2）。
    remote_landing_names = (
        ()
        if cancelled
        else tuple(
            name
            for name, ref in outcome.output_artifacts.items()
            if isinstance(ref, dict) and name in expected
        )
    )
    conflict = find_landing_conflict(
        job_dir=job_dir,
        view_dir=view_dir,
        staged_moves=staged_moves,
        remote_landing_names=remote_landing_names,
    )
    if conflict is not None:
        return handler.leases.finish(
            lease_id,
            ExecutionResult(
                status="failed",
                exit_code=1,
                error_message=conflict.full_message(),
                runner=worker_id,
                staged_file_moves=tuple(
                    (str(target), str(source))
                    for target, source in gate_safe_staged_moves(
                        staged_moves, job_dir=job_dir, excluding=conflict.names
                    )
                ),
            ),
            stage_timer=stage_timer,
        )
    # The read view must cover both channels: archive outputs live in the
    # staging dir, Worker-direct downloads already landed in job_dir via the
    # gated promote — hardlink the latter into the view (same FS, zero-copy)
    # so validation/shard-read/mirror see one unified dir.
    if view_dir is not job_dir:
        link_into_view(expected, job_dir, view_dir)
    # #160 D12: dict-form refs mean the Worker uploaded straight to S3
    # (per-execution staging keys); verify ALL refs, then promote +
    # download + register (no half-applied state). Any failure flips the
    # whole result to failed.
    remote_names, remote_failure = apply_worker_artifact_refs(
        handler.object_store,
        runner=worker_id,
        workspace_id=str(job["workspace_id"]),
        job_id=job_id,
        node_key=node_key,
        job_dir=job_dir,
        expected=expected,
        output_artifacts=outcome.output_artifacts,
        download=not cancelled,
        execution_id=str(manifest.get("execution_id") or ""),
        lease_id=lease_id,
        max_size_bytes=handler.max_archive_bytes,
        spot_check_percent=handler.spot_check_percent,
    )
    if remote_failure is not None:
        mark_result_stage(stage_timer, "artifacts_verify")
        # The staged promotion still rides the finish gate so the failed
        # node's node.log / events.jsonl land (observability parity with the
        # pre-staging behavior, #759 review P3) — staleness stays guarded.
        # 与冲突分支同一纪律：只挂闸安全的 moves（祖先挡位重检）——预检
        # 无锁，ref 验证期间现场可能已变坏，未过滤的被挡 move 会在闸内炸
        # 开并连带观测 moves 被整体回滚（#774 对抗复审 P2）。
        remote_failure = replace(
            remote_failure,
            staged_file_moves=tuple(
                (str(target), str(source))
                for target, source in gate_safe_staged_moves(staged_moves)
            ),
        )
        return handler.leases.finish(lease_id, remote_failure, stage_timer=stage_timer)
    # Refs applied after the view was built must be linked in too — with
    # OVERWRITE: the gated promote may have os.replaced a job_dir file the
    # first pass already linked (a same-generation re-execution leftover),
    # and the view must track THIS attempt's bytes, not the previous
    # inode's (#759 review P1).
    if view_dir is not job_dir:
        link_into_view(tuple(remote_names), job_dir, view_dir, overwrite=True)
    if remote_names and staged_moves:
        # A redundant Worker reporting the same name in BOTH the archive and
        # a dict-ref: the ref channel must win on every plane (#759 review
        # N2) — the remote promote already landed the ref bytes in job_dir,
        # so drop the archive's same-name move to keep the finish-gate
        # promotion from overwriting them with the archive copy (the
        # pre-staging "last writer wins" order).
        remote_targets = {job_dir / name for name in remote_names}
        staged_moves = [move for move in staged_moves if move[0] not in remote_targets]
    for name, ref in outcome.output_artifacts.items():
        if name not in remote_names:
            handler.artifact_store.add_ref(job_id, node_key, name, str(ref).split(":", 1)[-1])
    mark_result_stage(stage_timer, "artifacts_verify")
    produced = tuple(name for name in expected if (view_dir / name).is_file())
    status = outcome.status
    exit_code = outcome.exit_code
    error = outcome.error_message
    if status == "completed" and expected and not outcome.output_artifacts:
        status, exit_code, error = "failed", 1, "Agent Worker did not report output artifacts"
    missing = [name for name in expected if name not in produced]
    if status == "completed" and missing:
        status, exit_code, error = "failed", 1, f"Missing outputs: {', '.join(missing)}"
    # Worker results are untrusted: validate Host-side like the Pi runner.
    if status == "completed" and handler.skill_manager is not None:
        validation_error = validate_worker_outputs(handler.skill_manager, manifest, view_dir)
        if validation_error:
            status, exit_code, error = "failed", 1, validation_error
    mark_result_stage(stage_timer, "validate")
    # D12: mirror produced artifacts into object storage (best-effort —
    # a storage outage never flips the node; the reconciler retries).
    # #759 review P1-1: the mirror is armed with the lease so a stale
    # completion registers nothing and overwrites no authority object.
    if status == "completed" and produced:
        upload_produced_artifacts(
            handler.object_store,
            workspace_id=str(job["workspace_id"]),
            job_id=job_id,
            node_key=node_key,
            job_dir=view_dir,
            produced=produced,
            skip=remote_names,
            lease_id=lease_id,
        )
    mark_result_stage(stage_timer, "artifacts_upload")
    finished = handler.leases.finish(
        lease_id,
        ExecutionResult(
            status=status,
            exit_code=exit_code,
            error_message=error,
            command=outcome.command,
            # The promoted events.jsonl feeds log display and token usage
            # only; success/failure decisions above never read it.
            run_dir=stored_run_dir(handler, job_dir, outcome.run_dir, view_dir),
            session_dir="",
            skill_version=str(manifest.get("skill_version", "")),
            # #410 (v75): the binding identity for the studio latest-run
            # echo — skill_version's ref prefix is not the skill key.
            skill=str(manifest.get("skill", "")),
            produced_artifacts=produced,
            runner=worker_id,
            # Shard runs (#389): the per-shard payload rides the archive
            # as a regular expected output (shard_output-<index>.json);
            # read it from the staged view — the same file the local
            # executor would have produced, no size-capped metadata hop.
            output_json=read_shard_output(view_dir, manifest) if status == "completed" else "",
            # #759 review P1-1: the file promotion rides the finish
            # generation gate; a stale completion never lands a byte.
            staged_file_moves=tuple((str(target), str(source)) for target, source in staged_moves),
        ),
        stage_timer=stage_timer,
    )
    if finished and remote_names and handler.object_store is not None:
        # staging 源的唯一安全删除点（#774 对抗复审）：promote→finish 窗
        # 口内绝不删（并发重试仍要 verify/promote 同一份字节，先删会让
        # 后到者的失败 finish 抢跑冤判已完成节点）；finish 提交后迟到
        # 重试只会拿到 verdict False（409）。两个早退臂（预检冲突 /
        # remote_failure）与 verdict False 路径不删，残留由 GC 兜底。
        discard_staging_refs(handler.object_store, outcome.output_artifacts, remote_names)
    return finished


def stored_run_dir(
    handler: AgentCompletionHandler, job_dir: Path, run_dir: str, view_dir: Path | None = None
) -> str:
    """Data-dir-relative path of the promoted Worker run dir, or "".

    Empty when the Worker did not declare one or nothing was promoted, so
    older Workers and cancelled runs behave exactly as before. Existence is
    probed on ``view_dir`` (the staging read view, #759 review P1-1) while
    the recorded path stays job_dir-based — the finish gate moves the events
    file there before this value is read back."""
    probe = view_dir if view_dir is not None else job_dir
    run_dir_relative = safe_relative_dir(run_dir)
    if run_dir_relative is None or not (probe / run_dir_relative).is_dir():
        return ""
    base = handler.leases.data_dir or handler.jobs_dir.parent
    try:
        return (job_dir / run_dir_relative).resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return ""
