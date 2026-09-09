"""Filter 批次的崩溃恢复 staging（#532 PR-B / PR #545 P1）。

从 campaign_feeder.py 拆出（#209 预算棘轮，campaign_slice_queries 的
sibling 先例）：feeder 的 filter 形态有一条 explicit-ids 形态没有的
崩溃窗口——filter 的匹配字段会被投递自身改写（rerun 的
status="failed"、upgrade 的 status/workflow_version），进程在「批次已
提交」与「CAS 计数落账」之间死亡时，重启后重查 filter 选不出已投递的
job（它们退出了匹配集），processed/jobs 计数永久漏记。本模块把「本批
目标」在投递前 CAS 进 progress_json.pending_batch（stage），重启时优
先重放（replay），计数落账时摘除（pop）—— feeder 只保留编排，形状与
切片纪律同居于此。
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_errors import InvalidOperationError


def copy_progress(campaign: dict[str, Any]) -> dict[str, Any]:
    """Shallow copy of the campaign's stored progress document."""
    progress = campaign.get("progress")
    return dict(progress) if isinstance(progress, dict) else {}


def target_spec(campaign: dict[str, Any]) -> dict[str, Any]:
    """The stored target spec; a non-dict shape is a deterministic failure."""
    spec = campaign.get("target_spec")
    if not isinstance(spec, dict):
        raise InvalidOperationError(
            f"Campaign target spec is corrupt (expected an object, got {type(spec).__name__})"
        )
    return spec


def spec_filter(spec: dict[str, Any]) -> JobListFilter:
    """Rebuild the stored JobListFilter; unknown keys are a corrupt row."""
    raw = spec.get("filter")
    if not isinstance(raw, dict):
        raise InvalidOperationError("Campaign filter target is corrupt (expected an object)")
    try:
        return JobListFilter(**raw)
    except TypeError as exc:
        raise InvalidOperationError(f"Campaign filter target is corrupt: {exc}") from exc


def next_slice(job_db: Any, campaign: dict[str, Any]) -> tuple[list[str], str | None, bool]:
    """Next id slice for a rerun/upgrade campaign (design §1.4).

    Filter form: one keyset page of the stored filter; a page that returns
    no cursor is the last page. A staged pending_batch replays first (PR
    #545 P1). Explicit-ids form: the stored snapshot list at the offset.
    """
    spec = target_spec(campaign)
    progress = copy_progress(campaign)
    batch_size = int(campaign["batch_size"])
    if "filter" in spec:
        pending = progress.get("pending_batch")
        if isinstance(pending, dict) and pending.get("ids"):
            # PR #545 P1 恢复路径：优先消化上一进程 stage 的本批目标。
            # filter 的匹配字段会被投递自身改写，崩溃后已提交的 job 已
            # 退出匹配集——重查 filter 选不出同一批（漏记根因）；
            # pending_batch 的 ids + next_cursor 就是当时选定的切片，
            # 重放它（写路径守卫幂等吸收）并按其 next_cursor 推进。
            return (
                [str(value) for value in pending["ids"]],
                pending.get("next_cursor"),
                bool(pending.get("exhausted")),
            )
        ids, next_cursor = job_db.list_campaign_filter_job_ids_page(
            str(campaign["workspace_id"]),
            spec_filter(spec),
            batch_size,
            progress.get("cursor") or None,
        )
        return ids, next_cursor, next_cursor is None
    all_ids = [str(value) for value in (spec.get("job_ids") or [])]
    offset = int(progress.get("offset") or 0)
    slice_ids = all_ids[offset : offset + batch_size]
    return slice_ids, None, offset + len(slice_ids) >= len(all_ids)


def stage_batch(
    job_db: Any,
    campaign: dict[str, Any],
    ids: list[str],
    next_cursor: str | None,
    exhausted: bool,
) -> bool:
    """PR #545 P1：filter 批次在投递前先持久化「本批目标」（CAS）。

    崩溃窗口：filter 的匹配字段被投递自身改写，批次已提交而 advance
    未落账时进程死亡——重启后重查 filter 选不出已投递的 job，计数永
    久漏记。先 stage、后投递、落账时摘除（_advance_cursor），重启时
    next_slice 优先消化。False = CAS 输给 pause/cancel：批次不投、
    游标不动。explicit-ids 形态不 stage：快照列表不变，崩溃重放天然
    能选出同一批（回归锁 test_restart_refed_slice_absorbed_as_skips）。
    """
    if "filter" not in target_spec(campaign):
        return True
    progress = copy_progress(campaign)
    progress["pending_batch"] = {
        "ids": list(ids),
        "next_cursor": next_cursor,
        "exhausted": exhausted,
    }
    staged = job_db.advance_campaign_progress(
        str(campaign["id"]),
        expected_progress=copy_progress(campaign),
        progress=progress,
        batches_submitted=int(campaign["batches_submitted"]),
        jobs_succeeded=int(campaign["jobs_succeeded"]),
        jobs_skipped=int(campaign["jobs_skipped"]),
        jobs_failed=int(campaign["jobs_failed"]),
    )
    if staged is None:
        return False
    # 内存快照跟上 stage 后的文档：本 tick 后续 CAS 的
    # expected_progress 必须是含 pending_batch 的当前文档。
    campaign["progress"] = staged["progress"]
    return True
