import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { createRealtimeChannel } from '../lib/realtime'
import { queryKeys } from '../lib/queryKeys'
import type { WorkspaceStats } from '../types/workspaceTypes'

interface DashboardStatsPayload {
  type: string
  workspaces: Array<{ id: string } & WorkspaceStats>
}

export function useDashboardEvents(): void {
  const queryClient = useQueryClient()
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
            queryClient.setQueryData(queryKeys.workspaceStats(id), stats)
          }
        } catch {
          // ignore invalid payloads
        }
      },
    })
    return () => channel.close()
  }, [queryClient])
}
