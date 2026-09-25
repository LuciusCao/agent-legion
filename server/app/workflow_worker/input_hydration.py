"""Ready-gate input hydration for the workflow worker (issue #759 P1).

The local job_dir is an evictable cache (EXEC-ARTIFACT-STORE-001): the
maintenance thread reclaims local files once the ``job_artifacts`` manifest
row is durably registered, so "file gone but manifest row present" is a
normal state. The ready gate (``find_ready_nodes`` / ``evaluate_branches``)
only probes the local filesystem, so a manifest-only input parked the job at
queued forever — ``restore_missing_inputs`` only runs after a claim the job
could never reach.

This module closes the gap on the evaluation-miss path: for each evaluated
job it batch-fetches the manifest rows once and re-materializes the missing
locally declared inputs this round actually probes (``live_probe_names``:
node ``inputs`` with a runnable consumer ∪ branch-condition artifacts of
evaluatable edges, #759 review P1) with
the exact ``restore_missing_inputs`` semantics (``.part`` + sha256 +
``os.replace``, per-file best-effort). It deliberately stays OUT of the pure
``find_ready_nodes``; the caller (``eval_batch``) runs it before branch and
ready evaluation and defers — without caching — any job that still has
manifest-backed inputs missing after the attempt, so the next poll pass
retries (a missing object may be transient). The same defer-without-cache
discipline covers the two READ failures (manifest query, generation
pre-read): the manifest is the authoritative artifact copy
(EXEC-ARTIFACT-STORE-001), so a read failure must never be cached as a true
local miss — that would park the job at queued forever, the scan mark being
unchanged once the fault clears.

Threading: the poll thread evaluates jobs sequentially, and
``JobArtifactObjectStore`` is already shared across the route/maintenance/
claim threads (per-call pooled connections), so sharing one instance here
follows the existing usage contract.

Race with reset mutations (#702 review P1): hydration deliberately does NOT
take the ``job-mutation:<job_id>`` advisory lock — the object-store downloads
between the manifest read and the final ``os.replace`` can take seconds and
would block every rerun/upgrade of the job. Instead it brackets the restore
with two reads of ``jobs.execution_generation`` (EXEC-GENERATION-001): every
reset mutation bumps the epoch in the same transaction that deletes the reset
outputs' manifest rows, so an epoch change observed after the writes means
the restored bytes came from a manifest the mutation has since invalidated —
the files this round restored are deleted again and the job defers (uncached)
to the next poll pass. Rounds that restore NOTHING skip the recheck read
(#759 review P1: a running job is re-evaluated every poll pass, and the
per-job double read with an empty write set degrades the scan into a steady
O(running jobs) N+1); with no restores there is nothing to invalidate, and
epoch invalidation still rides the mark_key / claim-time CAS.

Residual window: a mutation can still commit AFTER a passing recheck, leaving
a stale restored file on disk for the new epoch. That is safe on three
grounds: (1) candidates built this round carry the pre-bump epoch — the
mutation bumps the mark_key (``scan.mark_key`` includes the epoch), so the
cache is invalidated and the claim-time generation CAS fails closed on any
stale candidate; (2) the next pass re-evaluates and hydration never restores
the name again (its manifest row is gone); (3) the surviving stale file
cannot drive any consumer's verdict in the new epoch: a deleted-row name has
a reset (non-terminal) producer, and every consumer channel is barriered on
non-terminal producers — node inputs via
``scheduler._has_unfinished_implicit_producer``, branch-condition artifacts
via ``condition_barrier.condition_producer_in_flight`` (#759 ③ 对抗复审
P1) — so every consumer defers until the producer re-runs and overwrites the
file. The window is the millisecond-scale staging→commit span of one
transaction, per file.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from server.app.executors.artifact_restore import restore_from_manifest_row
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_branching import RUNNABLE_STATUSES, effective_node_statuses
from server.app.workflows.workflow_consumption import artifact_consumption_index

if TYPE_CHECKING:
    from server.app.jobs import JobQueries
    from server.app.services.job_artifact_objects import JobArtifactObjectStore

logger = logging.getLogger(__name__)


def live_probe_names(
    definition: WorkflowDefinition, node_statuses: dict[str, str]
) -> frozenset[str]:
    """本轮评估真正会本地探针的产物名（#759 复审 P1）：共享消费索引
    （``artifact_consumption_index``，唯一枚举处）按当前 node statuses
    收窄——

    - 节点 ``inputs``：至少一个消费者处于 RUNNABLE_STATUSES
      （``find_ready_nodes`` 只为可运行节点探 inputs 与入边选择）；
    - ``edge.condition.artifact``：target 可运行（就绪闸探入边选择）或
      source 为 completed（``evaluate_branches`` 逐 completed source 裁决，
      条件文件在场与否决定 not_applicable 标记， verdict 必须稳定）。

    终态分支的消费名由此退出恢复/defer 集：已完成并被淘汰缓存的 job 做单
    分支 targeted rerun 时，其他终态分支永久丢失/损坏的对象不再把整个
    job 的评估卡死在 defer 上（复审前恢复面是索引键集全集，任一历史消费
    项都是全 job 屏障）。状态是每轮现场传入的，未来某轮 rerun 把终态节
    点重置回可运行时其消费名自动回到探针集。
    """
    statuses = effective_node_statuses(definition, node_statuses)
    runnable = {key for key, status in statuses.items() if status in RUNNABLE_STATUSES}
    names = {
        name
        for name, consumers in artifact_consumption_index(definition).items()
        if consumers & runnable
    }
    names.update(
        edge.condition.artifact
        for edge in definition.edges
        if edge.condition is not None and statuses.get(edge.source) == "completed"
    )
    return frozenset(names)


def hydrate_job_artifacts(
    store: JobArtifactObjectStore | None,
    queries: JobQueries,
    *,
    job_id: str,
    job_dir: Path,
    definition: WorkflowDefinition,
    node_statuses: dict[str, str],
) -> frozenset[str] | None:
    """Re-materialize manifest-backed inputs missing from the job_dir.

    Best-effort; no-op without a configured object store. The restore/defer
    surface is ``live_probe_names`` — the consumption-index names this
    round's evaluation actually probes given current node statuses (#759
    review P1: the pre-review surface was the whole definition's index key
    set, so a permanently lost object consumed only by terminal sibling
    branches held every targeted rerun of the job hostage). Returns the
    names that STILL lack a local file despite having a manifest row
    (restore failed / object missing / discarded by the generation recheck)
    — the caller must not cache the evaluation of such a job, so the next
    poll pass retries. Inputs with no manifest row are genuinely absent and
    are not hydration's business: they are left out of the returned set so
    the job evaluates (and caches) as not-ready.

    ``None`` means the evaluation basis itself could not be read: the
    manifest query failed, or the pre-read of ``jobs.execution_generation``
    failed / the job row is gone. The object-store manifest is the
    authoritative artifact copy (EXEC-ARTIFACT-STORE-001), so a read failure
    must NOT degrade to "evaluate with local files only" — a local miss
    would then be cached as a true miss and, the scan mark being unchanged,
    the manifest would never be re-read after the fault clears (the
    parked-at-queued-forever regression). The caller treats ``None`` exactly
    like a non-empty unrestored set: no caching, no candidates, retry next
    pass. A SUCCESSFUL read that finds no manifest row for a name is not
    deferred — it is the genuine-absent case above.

    Generation bracket (#702 review P1): the epoch is read before the
    manifest query and again after every restore write; a mismatch means a
    reset mutation committed mid-flight and invalidated the manifest rows
    this round restored from, so exactly those files are deleted again and
    returned as unrestored (defer, never cache). Rounds whose restore set is
    EMPTY skip the recheck read (#759 review P1) — there are no restored
    bytes to invalidate, and skipping it halves the per-job query cost of
    re-evaluating running jobs every poll pass. See the module docstring
    for the residual-window argument.
    """
    if store is None or not store.enabled:
        return frozenset()
    missing = [
        name
        for name in live_probe_names(definition, node_statuses)
        if not (job_dir / name).is_file()
    ]
    if not missing:
        return frozenset()
    generation_before = _current_generation(queries, job_id)
    if generation_before is None:
        # Job row gone mid-pass, or the read failed: the manifest state is
        # unknowable, so defer (uncached) rather than cache a local-files-only
        # evaluation that would stick after the fault clears.
        return None
    try:
        rows = store.rows_for_job(job_id)
    except Exception:
        # #204 broad-except audit: the manifest read is the one failure the
        # per-file containment cannot see (it happens before any per-file
        # work). The outcome space is the psycopg/pool surface of that one
        # query. Returning None defers the job WITHOUT caching (same
        # discipline as an incomplete restore) so the next poll pass re-reads
        # the manifest once the outage clears; the traceback is logged so the
        # deferral stays visible.
        logger.warning(
            "artifact manifest read failed for job %s; deferring evaluation",
            job_id,
            exc_info=True,
        )
        return None
    # rows_for_job 按 (uploaded_at, node_key) 升序；dict 留尾 = 同名取决胜
    # 序的最大行，与 lookup() 的「最新」判定同源（#775 对抗复审 P2——并列
    # 时间戳下两条读路径曾可能选中不同行，把 hydration 卡进永久 defer）。
    rows_by_name = {str(row["name"]): row for row in rows}
    unrestored: set[str] = set()
    for name in missing:
        if name in rows_by_name and not restore_from_manifest_row(
            store, job_id=job_id, job_dir=job_dir, name=name, row=rows_by_name[name]
        ):
            unrestored.add(name)
    restored = {name for name in missing if name in rows_by_name} - unrestored
    if not restored:
        # 本轮零恢复写：代次复核没有保护对象，跳过第二次代次读（#759 复审
        # P1——running job 每轮绕过评估缓存重评，无可恢复清单行时的逐 job
        # 双读会把扫描拖成持续的 O(运行中 job 数) N+1 查询）。代次中途变化
        # 无需检测：候选/mark 失效由代次进 mark_key 与 claim CAS 兜底，
        # unrestored 非空时本就不缓存。
        return frozenset(unrestored)
    generation_after = _current_generation(queries, job_id)
    if generation_after == generation_before:
        return frozenset(unrestored)
    # A reset mutation committed between the two reads (or the job row
    # vanished / the recheck failed): the restored bytes may come from a
    # manifest row the mutation has since invalidated. Delete exactly the
    # files this round restored — nothing else in the job_dir is ours to
    # touch. If a new-epoch producer re-wrote the same name in between,
    # deleting it is still safe: its fresh manifest row re-hydrates it on
    # the next pass.
    for name in restored:
        try:
            (job_dir / name).unlink(missing_ok=True)
        except OSError:
            # Contained to the filesystem surface of one unlink: a removal
            # failure must not skip the remaining files. The stale file is
            # still caught by the claim-time generation CAS and the next
            # pass's re-evaluation (module docstring, residual window).
            logger.warning(
                "failed to discard stale restored input %s for job %s", name, job_id, exc_info=True
            )
    logger.warning(
        "job %s generation changed during hydration (%s -> %s); discarded restored inputs %s",
        job_id,
        generation_before,
        generation_after,
        sorted(restored),
    )
    return frozenset(unrestored | restored)


def _current_generation(queries: JobQueries, job_id: str) -> int | None:
    """Live ``jobs.execution_generation``; None when the row is gone or the read failed."""
    try:
        return queries.get_job_execution_generation(job_id)
    except Exception:
        # #204 broad-except audit: None collapses "job deleted" and "transient
        # DB outage" because both callers want the same outcome for each —
        # the pre-write read defers the job without caching (never a sticky
        # local-files-only evaluation), the post-write recheck fails closed
        # (mismatch ⇒ discard this round's files; self-healing, the next pass
        # restores them again). The outcome space is the psycopg/pool surface
        # of one query; exc_info keeps the root cause visible.
        logger.warning("generation read failed for job %s", job_id, exc_info=True)
        return None
