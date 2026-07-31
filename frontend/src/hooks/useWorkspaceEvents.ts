import { useEffect, useRef } from 'react'
import { useJobStore } from '../stores/jobStore'
import { useExecutorsStore } from '../stores/executorsStore'
import { createRealtimeChannel } from '../lib/realtime'
import { handleWorkspaceEvent } from './workspaceEventHandlers'
import { refreshWorkspaceEvents } from './workspaceEventRefresh'
import { refreshJobFacets } from './workspaceFacetsRefresh'
import {
  createLoadSnapshot,
  enqueuePendingEvent,
} from './workspaceSnapshotLoader'
export function useWorkspaceEvents(
  workspaceId: string | undefined,
  enabled = true,
  statsOnly = false
) {
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
      refreshWorkspaceEvents(workspaceId, () => stale || closed)

    const scheduleJobRefresh = () => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
      refreshTimerRef.current = setTimeout(() => {
        refreshTimerRef.current = null
        // Job list changes arrive as job_patch_batch; refresh stats + facets.
        void refresh()
        void refreshJobFacets(workspaceId, statsOnly, () => stale || closed)
        // Worker assignment may change with job updates (same debounce tier).
        void useExecutorsStore.getState().refreshWorkers()
      }, jobUpdateRefreshDelay)
    }

    const processEvent = (event: MessageEvent) => {
      handleWorkspaceEvent(
        event,
        workspaceId,
        statsOnly,
        scheduleJobRefresh,
        loadSnapshot,
        () => void refresh()
      )
    }

    const loadSnapshot = createLoadSnapshot(
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
  }, [enabled, workspaceId, statsOnly])
}
