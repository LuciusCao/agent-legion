import { useEffect, useRef } from 'react'
import { useJobStore } from '../stores/jobStore'
import { handleWorkspaceEvent } from './workspaceEventHandlers'
import { refreshWorkspaceEvents } from './workspaceEventRefresh'
import {
  createLoadSnapshot,
  enqueuePendingEvent,
} from './workspaceSnapshotLoader'
export function useWorkspaceEvents(
  workspaceId: string | undefined,
  enabled = true,
  statsOnly = false
) {
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const snapshotLoadingRef = useRef(!statsOnly)
  const pendingEventsRef = useRef<MessageEvent[]>([])
  useEffect(() => {
    if (workspaceId) useJobStore.getState().resetForWorkspace(workspaceId)
  }, [workspaceId])
  useEffect(() => {
    if (!enabled || !workspaceId || typeof EventSource === 'undefined') return

    let source: EventSource | null = null
    let reconnectDelay = 1000
    const maxReconnectDelay = 30000
    const jobUpdateRefreshDelay = 750
    const maxPendingEvents = 1000
    let closed = false
    let stale = false
    const refresh = (includeJobs: boolean) =>
      refreshWorkspaceEvents(
        workspaceId,
        includeJobs,
        statsOnly,
        () => stale || closed
      )

    const scheduleJobRefresh = () => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
      refreshTimerRef.current = setTimeout(() => {
        refreshTimerRef.current = null
        void refresh(true)
      }, jobUpdateRefreshDelay)
    }

    const processEvent = (event: MessageEvent) => {
      handleWorkspaceEvent(
        event,
        workspaceId,
        statsOnly,
        scheduleJobRefresh,
        loadSnapshot,
        () => void refresh(false)
      )
    }

    const loadSnapshot = createLoadSnapshot(
      workspaceId,
      snapshotLoadingRef,
      pendingEventsRef,
      processEvent,
      () => stale || closed
    )

    const connect = () => {
      if (source || closed || stale) return
      source = new EventSource(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/events`
      )
      source.onopen = () => {
        reconnectDelay = 1000
        if (statsOnly) {
          snapshotLoadingRef.current = false
          void refresh(false)
        } else {
          void loadSnapshot()
        }
      }
      source.onmessage = (event) => {
        if (snapshotLoadingRef.current) {
          enqueuePendingEvent(pendingEventsRef, event, maxPendingEvents)
        } else {
          processEvent(event)
        }
      }
      source.onerror = () => {
        source?.close()
        source = null
        if (closed || stale) return
        reconnectTimerRef.current = setTimeout(() => {
          reconnectDelay = Math.min(reconnectDelay * 2, maxReconnectDelay)
          connect()
        }, reconnectDelay)
      }
    }

    connect()
    return () => {
      stale = true
      closed = true
      for (const timer of [
        reconnectTimerRef.current,
        refreshTimerRef.current,
      ]) {
        if (timer) clearTimeout(timer)
      }
      if (source) {
        source.close()
      }
    }
  }, [enabled, workspaceId, statsOnly])
}
