import { useMemo } from 'react'
import {
  jobBatchOpToCard,
  runBatchToCard,
  type TaskCardModel,
} from './taskCardModel'
import { useActiveRunBatches, useRunBatchWaterfall } from './useRunBatches'
import {
  useActiveJobBatchOps,
  useJobBatchOpWaterfall,
} from './useJobBatchOpListing'

/**
 * 任务中心双源编排（#946 M52 Phase 2）：run_batches（批量创建）与
 * job_batch_operations（批量操作）的卡片在同一列表按时间混排，三视图
 * （active/ended/archived）口径一致。两个 ended/archived 瀑布流各自翻页，
 * 混排后「加载更早」同时推进尚有下一页的来源。
 */

const timeMs = (iso: string | null): number => (iso ? Date.parse(iso) || 0 : 0)

/** active 混排（M44 裁决）：进行中/已暂停按发起时间倒序在前，排队按提交
 * 顺序（升序）在后。 */
export function sortActiveCards(cards: TaskCardModel[]): TaskCardModel[] {
  const live = cards.filter(
    (card) => card.status === 'running' || card.status === 'paused'
  )
  const queued = cards.filter(
    (card) => card.status !== 'running' && card.status !== 'paused'
  )
  live.sort((a, b) => timeMs(b.startedAt) - timeMs(a.startedAt))
  queued.sort((a, b) => timeMs(a.startedAt) - timeMs(b.startedAt))
  return [...live, ...queued]
}

/** ended/archived 混排：按结束时间倒序。 */
export function sortEndedCards(cards: TaskCardModel[]): TaskCardModel[] {
  return [...cards].sort((a, b) => timeMs(b.finishedAt) - timeMs(a.finishedAt))
}

export function useTaskCenterCards(
  workspaceId: string | undefined,
  archivedView: boolean
) {
  const activeBatches = useActiveRunBatches(workspaceId)
  const activeOps = useActiveJobBatchOps(workspaceId)
  const endedBatches = useRunBatchWaterfall(workspaceId, 'ended', !archivedView)
  const endedOps = useJobBatchOpWaterfall(workspaceId, 'ended', !archivedView)
  const archivedBatches = useRunBatchWaterfall(
    workspaceId,
    'archived',
    archivedView
  )
  const archivedOps = useJobBatchOpWaterfall(
    workspaceId,
    'archived',
    archivedView
  )

  const activeCards = useMemo(
    () =>
      sortActiveCards([
        ...(activeBatches.data?.batches ?? []).map(runBatchToCard),
        ...(activeOps.data?.operations ?? []).map(jobBatchOpToCard),
      ]),
    [activeBatches.data, activeOps.data]
  )
  const endedCards = useMemo(
    () =>
      sortEndedCards([
        ...(endedBatches.data?.pages ?? []).flatMap((page) =>
          page.batches.map(runBatchToCard)
        ),
        ...(endedOps.data?.pages ?? []).flatMap((page) =>
          page.operations.map(jobBatchOpToCard)
        ),
      ]),
    [endedBatches.data, endedOps.data]
  )
  const archivedCards = useMemo(
    () =>
      sortEndedCards([
        ...(archivedBatches.data?.pages ?? []).flatMap((page) =>
          page.batches.map(runBatchToCard)
        ),
        ...(archivedOps.data?.pages ?? []).flatMap((page) =>
          page.operations.map(jobBatchOpToCard)
        ),
      ]),
    [archivedBatches.data, archivedOps.data]
  )

  /** 双源「加载更早」：推进一切尚有下一页的来源。 */
  const fetchNextEndedPage = () => {
    if (endedBatches.hasNextPage) void endedBatches.fetchNextPage()
    if (endedOps.hasNextPage) void endedOps.fetchNextPage()
  }
  const fetchNextArchivedPage = () => {
    if (archivedBatches.hasNextPage) void archivedBatches.fetchNextPage()
    if (archivedOps.hasNextPage) void archivedOps.fetchNextPage()
  }

  return {
    activeCards,
    endedCards,
    archivedCards,
    activeCount:
      (activeBatches.data?.active_count ?? 0) +
      (activeOps.data?.active_count ?? 0),
    isLoading:
      activeBatches.isLoading ||
      activeOps.isLoading ||
      endedBatches.isLoading ||
      endedOps.isLoading,
    isArchivedLoading: archivedBatches.isLoading || archivedOps.isLoading,
    ended: {
      hasNextPage:
        Boolean(endedBatches.hasNextPage) || Boolean(endedOps.hasNextPage),
      isFetchingNextPage:
        endedBatches.isFetchingNextPage || endedOps.isFetchingNextPage,
      fetchNextPage: fetchNextEndedPage,
    },
    archived: {
      hasNextPage:
        Boolean(archivedBatches.hasNextPage) ||
        Boolean(archivedOps.hasNextPage),
      isFetchingNextPage:
        archivedBatches.isFetchingNextPage || archivedOps.isFetchingNextPage,
      fetchNextPage: fetchNextArchivedPage,
    },
  }
}
