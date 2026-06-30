import { useEffect, useRef } from 'react'
import { useJobStore } from '../stores/jobStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { fetchJobs, fetchWorkspaceStats } from '../api'

interface WorkspaceEventPayload {
  type: string
  workspace_id: string
  job_id?: string
  jobs?: Array<Record<string, unknown>>
  stats?: Record<string, number>
}

export function useWorkspaceEvents(
  workspaceId: string | undefined,
  enabled = true,
  statsOnly = false
) {
  const setJobs = useJobStore((state) => state.setJobs)
  const setWorkspaceStats = useWorkspaceStore(
    (state) => state.setWorkspaceStats
  )
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!enabled || !workspaceId || typeof EventSource === 'undefined') return

    let source: EventSource | null = null
    let reconnectDelay = 1000
    const maxReconnectDelay = 30000
    let closed = false
    let stale = false

    const refresh = async (includeJobs: boolean) => {
      try {
        const stats = await fetchWorkspaceStats(workspaceId)
        if (stale || closed) return
        setWorkspaceStats(workspaceId, stats)
        if (includeJobs && !statsOnly) {
          const jobsData = await fetchJobs(workspaceId)
          if (stale || closed) return
          setJobs(jobsData.jobs)
        }
      } catch {
        // ignore refresh errors
      }
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
            setWorkspaceStats(workspaceId, {
              ...useWorkspaceStore.getState().workspaceStats[workspaceId],
              job_stats: payload.stats,
            })
          }
          refresh(true)
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
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
      }
      if (source) {
        source.close()
      }
    }
  }, [enabled, workspaceId, statsOnly, setJobs, setWorkspaceStats])
}
