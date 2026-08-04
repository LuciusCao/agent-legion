import { useEffect } from 'react'
import { useWorkspaceStore } from '../stores/workspaceStore'
import { createRealtimeChannel } from '../lib/realtime'
import type { WorkspaceStats } from '../types/workspaceTypes'

interface DashboardStatsPayload {
  type: string
  workspaces: Array<{ id: string } & WorkspaceStats>
}

export function useDashboardEvents(): void {
  useEffect(() => {
    if (typeof EventSource === 'undefined') return
    const channel = createRealtimeChannel({
      url: '/api/dashboard/events',
      protocol: 'sse',
      onEvent: (_type, data) => {
        if (!data || data.startsWith(':heartbeat')) return
        try {
          const payload = JSON.parse(data) as DashboardStatsPayload
          if (payload.type !== 'workspace_stats_batch') return
          for (const workspace of payload.workspaces) {
            const { id, ...stats } = workspace
            useWorkspaceStore.getState().setWorkspaceStats(id, stats)
          }
        } catch {
          // ignore invalid payloads
        }
      },
    })
    return () => channel.close()
  }, [])
}
