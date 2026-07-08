import { useEffect } from 'react'
import { useWorkspaceStore } from '../stores/workspaceStore'
import type { WorkspaceStats } from '../workspaceTypes'

interface DashboardStatsPayload {
  type: string
  workspaces: Array<{ id: string } & WorkspaceStats>
}

export function useDashboardEvents(): void {
  useEffect(() => {
    if (typeof EventSource === 'undefined') return
    const source = new EventSource('/api/dashboard/events')
    source.onmessage = (event) => {
      if (!event.data || event.data.startsWith(':heartbeat')) return
      try {
        const payload = JSON.parse(event.data) as DashboardStatsPayload
        if (payload.type !== 'workspace_stats_batch') return
        for (const workspace of payload.workspaces) {
          const { id, ...stats } = workspace
          useWorkspaceStore.getState().setWorkspaceStats(id, stats)
        }
      } catch {
        // ignore invalid payloads
      }
    }
    return () => source.close()
  }, [])
}
