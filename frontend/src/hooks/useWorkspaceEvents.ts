import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useJobStore } from '../stores/jobStore'
import { createRealtimeChannel } from '../lib/realtime'
import { invalidateAgentWorkers } from '../lib/agentWorkersInvalidation'
import { handleWorkspaceEvent } from './workspaceEventHandlers'
import { refreshWorkspaceEvents } from './workspaceEventRefresh'
import { refreshJobFacets } from './workspaceFacetsRefresh'
import {
  createLoadSnapshot,
  enqueuePendingEvent,
} from './workspaceSnapshotLoader'

/** #410（codex 四轮 P2 on #427）：node 更新事件（job_patch_batch /
 * job_updated）→ 失效 nodeRuns 前缀。检查器在节点运行前已挂载时查询缓存
 * 空记录，没有任何运行完成路径失效该 key——节点完成运行时「实际执行」
 * 仍为空，只能等窗口聚焦或重挂载。与 stats/facets 同一防抖层合并
 * （invalidateQueries 只对有活跃观察者的查询触发 refetch，无观察者时不
 * 发请求）。 */
function invalidateNodeRuns(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ['nodeRuns'] })
}

export function useWorkspaceEvents(
  workspaceId: string | undefined,
  enabled = true,
  statsOnly = false
) {
  const queryClient = useQueryClient()
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const snapshotLoadingRef = useRef(!statsOnly)
  const pendingEventsRef = useRef<MessageEvent[]>([])
  useEffect(() => {
    if (workspaceId) useJobStore.getState().resetForWorkspace(workspaceId)
  }, [workspaceId])
  useEffect(() => {
    if (!enabled || !workspaceId || typeof EventSource === 'undefined') return

    const jobUpdateRefreshDelay = 750
    const maxPendingEvents = 1000
    let closed = false
    let stale = false
    const refresh = () =>
      refreshWorkspaceEvents(queryClient, workspaceId, () => stale || closed)

    const scheduleJobRefresh = () => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
      refreshTimerRef.current = setTimeout(() => {
        refreshTimerRef.current = null
        // Job list changes arrive as job_patch_batch; refresh stats + facets.
        void refresh()
        void refreshJobFacets(workspaceId, statsOnly, () => stale || closed)
        // Worker assignment may change with job updates (same debounce tier).
        invalidateAgentWorkers(queryClient)
        // Node runs (inspector latest-version echo) change with job updates
        // too (codex four-pass P2 on #427); same debounce tier.
        invalidateNodeRuns(queryClient)
      }, jobUpdateRefreshDelay)
    }

    const processEvent = (event: MessageEvent) => {
      handleWorkspaceEvent(
        queryClient,
        event,
        workspaceId,
        statsOnly,
        scheduleJobRefresh,
        loadSnapshot,
        () => void refresh()
      )
    }

    const loadSnapshot = createLoadSnapshot(
      queryClient,
      workspaceId,
      snapshotLoadingRef,
      pendingEventsRef,
      processEvent,
      () => stale || closed
    )

    const channel = createRealtimeChannel({
      url: `/api/workspaces/${encodeURIComponent(workspaceId)}/events`,
      protocol: 'sse',
      onEvent: (_type, data) => {
        const event = new MessageEvent('message', { data })
        if (snapshotLoadingRef.current) {
          if (!enqueuePendingEvent(pendingEventsRef, event, maxPendingEvents)) {
            // Queue overflowed: resync from a fresh snapshot instead of losing patch revisions.
            pendingEventsRef.current = []
            void loadSnapshot()
          }
        } else {
          processEvent(event)
        }
      },
      onStatus: (status) => {
        if (status !== 'open') return
        if (statsOnly) {
          snapshotLoadingRef.current = false
          void refresh()
        } else {
          void loadSnapshot()
        }
      },
    })

    return () => {
      stale = true
      closed = true
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
      channel.close()
    }
  }, [enabled, workspaceId, statsOnly, queryClient])
}
