import { useEffect, useRef } from 'react'
import { useJobStore } from '../stores/jobStore'
import {
  mergeWorkspaceEventStats,
  refreshWorkspaceEvents,
} from './workspaceEventRefresh'

interface WorkspaceEventPayload {
  type: string
  workspace_id: string
  job_id?: string
  stats?: Record<string, number>
}

export function useWorkspaceEvents(
  workspaceId: string | undefined,
  enabled = true,
  statsOnly = false
) {
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (workspaceId) useJobStore.getState().resetForWorkspace(workspaceId)
  }, [workspaceId])

  useEffect(() => {
    if (!enabled || !workspaceId || typeof EventSource === 'undefined') return

    let source: EventSource | null = null
    let reconnectDelay = 1000
    const maxReconnectDelay = 30000
    const jobUpdateRefreshDelay = 750
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

    const connect = () => {
      if (source || closed || stale) return
      source = new EventSource(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/events`
      )
      source.onopen = () => {
        reconnectDelay = 1000
        refresh(true)
      }
      source.onmessage = (event) => {
        if (!event.data || event.data.startsWith(':heartbeat')) return
        try {
          const payload = JSON.parse(event.data) as WorkspaceEventPayload
          if (payload.workspace_id !== workspaceId) return
          if (payload.stats) {
            mergeWorkspaceEventStats(workspaceId, payload.stats)
          }
          if (payload.type === 'job_updated') {
            scheduleJobRefresh()
            return
          }
          void refresh(true)
        } catch {
          // ignore invalid payloads
        }
      }

      source.onerror = () => {
        if (source) {
          source.close()
          source = null
        }
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
