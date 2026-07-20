import { create } from 'zustand'
import { api } from '../api'
import type { components } from '../generated/api'
import type { ConnectionStatus } from '../lib/realtime'

export type WorkerSummary = components['schemas']['RemoteWorkerSummaryResponse']
type WorkersResponse = components['schemas']['WorkersResponse']

// Same debounce tier as the workspace job refresh (750ms).
const WORKERS_REFRESH_DELAY_MS = 750

interface ExecutorsState {
  workers: WorkerSummary[]
  connectionStatus: Record<string, ConnectionStatus>
  refreshWorkers: () => Promise<void>
  setConnectionStatus: (channel: string, status: ConnectionStatus) => void
}

let refreshTimer: ReturnType<typeof setTimeout> | null = null

export const useExecutorsStore = create<ExecutorsState>((set) => ({
  workers: [],
  connectionStatus: {},

  refreshWorkers: () => {
    if (refreshTimer) clearTimeout(refreshTimer)
    return new Promise<void>((resolve) => {
      refreshTimer = setTimeout(() => {
        refreshTimer = null
        void (async () => {
          try {
            const data = await api<WorkersResponse>('/api/remote/workers')
            set({ workers: data.workers ?? [] })
          } catch {
            // Keep the last known workers on failure.
          } finally {
            resolve()
          }
        })()
      }, WORKERS_REFRESH_DELAY_MS)
    })
  },

  setConnectionStatus: (channel, status) =>
    set((state) => ({
      connectionStatus: { ...state.connectionStatus, [channel]: status },
    })),
}))
