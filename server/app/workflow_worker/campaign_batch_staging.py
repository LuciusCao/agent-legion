"""Filter 批次的崩溃恢复 staging（#532 PR-B / PR #545 P1）。

feeder 的 filter 形态：匹配字段被投递自身改写，进程在「批次已提交」
与「CAS 计数落账」之间死亡时，重启后重查 filter 选不出已投递的 job
——本模块把「本批目标」在投递前 CAS 进 progress_json.pending_batch
（stage），重启时优先重放（replay），计数落账时摘除（pop）。

崩溃边界收口（round-3/4）：末页耗尽标记与 ``cursor=None`` 同一 CAS
原子落库；重放是否重投由 v81 投递标记归属，completed 检查只保留为产
物保护（语义见 campaign_feeder / modes）。
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_errors import InvalidOperationError

# stage 的 CAS 不动计数（投递前的快照锚点）——四个计数列的键名与
# advance_campaign_progress 的 kwargs 一一对应。
_COUNTER_KEYS = ("batches_submitted", "jobs_succeeded", "jobs_skipped", "jobs_failed")


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


def spec_exclude_ids(spec: dict[str, Any]) -> tuple[str, ...]:
    """Filter 形态的用户排除项（allMatching 反选）；显式 ids 形态不存储。
    异常形状按空处理——行损坏由 spec_filter 显式报错，此处不重复设卡。"""
    raw = spec.get("exclude_ids")
    if not isinstance(raw, list):
        return ()
    return tuple(str(v) for v in raw if str(v).strip())


def cursor_exhausted(progress: dict[str, Any]) -> bool:
    """Distinguish the initial ``cursor=None`` from the consumed one (PR #545
    round-3 P1-2).

    A filter campaign's progress starts as ``{"cursor": None}`` and a
    fully-drained keyset also parks ``None`` there — same value, opposite
    meanings. The crash window between the final advance (cursor=None,
    pending_batch popped) and the completed flip used to collapse them:
    a restart resumed at the INITIAL cursor and re-fed the whole set. The
    ``exhausted`` flag rides the same progress document the final CAS
    commits, so the marker and the None-cursor land in ONE atomic write.
    Only the filter form's marker matters; explicit-ids shares the flag for
    a uniform document shape but never reads it (offset arithmetic carries
    its own exhaustion).
    """
    return bool(progress.get("exhausted"))


def next_slice(job_db: Any, campaign: dict[str, Any]) -> tuple[list[str], str | None, bool]:
    """Next id slice for a rerun/upgrade campaign (design §1.4).

    Filter form: one keyset page of the stored filter; a page that returns
    no cursor is the last page. A staged pending_batch replays first (PR
    #545 P1), and an exhausted marker outranks it (round-3 P1-2: the final
    batch already landed its advance — the row is done, the marker is the
    crash-window stand-in for the completed flip that never got to run).
    Explicit-ids form: the stored snapshot list at the offset.
    """
    spec = target_spec(campaign)
    progress = copy_progress(campaign)
    batch_size = int(campaign["batch_size"])
    if "filter" in spec:
        if cursor_exhausted(progress):
            # The final page's advance committed (cursor=None + exhausted)
            # and only the completed flip crashed; rescan from page one
            # would re-feed every already-processed id for the search-like
            # filters rerun never rewrites. Slice stays empty.
            return [], None, True
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
            spec_exclude_ids(spec),
        )
        return ids, next_cursor, next_cursor is None
    all_ids = [str(value) for value in (spec.get("job_ids") or [])]
    offset = int(progress.get("offset") or 0)
    slice_ids = all_ids[offset : offset + batch_size]
    return slice_ids, None, offset + len(slice_ids) >= len(all_ids)


def partition_replay(
    job_db: Any, campaign_id: str, ids: list[str]
) -> tuple[set[str], set[str], list[str]]:
    """Batch replay partition: (delivered, completed, to_deliver) (round-4 P1).

    两条正交守卫的取数与分拣同居于此：
    ① v81 投递标记（campaign_job_deliveries，标记在翻转事务内原子落库）
    是归属权威——存在即「本 campaign 的翻转已提交」，重放计 succeeded
    （首投语义，只差落账），不再重投；已再次 failed 的 job 与「上次没落
    上的 failed」在 job 当前 status 上无法区分，标记是唯一可靠证据。
    ② completed 检查是产物保护——eligibility 对显式 node_key 放行
    completed 且无 lease 的 job，投递会清掉产物；作用于首投与无标记重放
    （v81 升级时在途的旧 campaign）两路，计 skipped。
    """
    delivered = job_db.campaign_delivered_job_ids(campaign_id)
    rows = job_db.list_job_rerun_states_for_jobs("", list(ids))
    completed = {i for i in ids if str(rows.get(i, {}).get("status")) == "completed"}
    to_deliver = [i for i in ids if i not in delivered and i not in completed]
    return delivered, completed, to_deliver


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
    counters = {k: int(campaign[k]) for k in _COUNTER_KEYS}
    staged = job_db.advance_campaign_progress(
        str(campaign["id"]),
        expected_progress=copy_progress(campaign),
        progress=progress,
        **counters,
    )
    if staged is None:
        return False
    # 内存快照跟上 stage 后的文档：本 tick 后续 CAS 的
    # expected_progress 必须是含 pending_batch 的当前文档。
    campaign["progress"] = staged["progress"]
    return True
