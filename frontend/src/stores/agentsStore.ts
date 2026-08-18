import { create } from 'zustand'
import { api } from '../api'
import { createRealtimeChannel, type RealtimeChannel } from '../lib/realtime'
import { parseAgentsWsMessage, upsertAgent } from '../lib/agentsWsMessages'
import { useConnectionStatusStore } from './connectionStatusStore'
import type { AgentStatus, WorkerStatusResponse } from '../types'

export interface AgentsState {
  agents: AgentStatus[]
  workerPausedByWorkspace: Record<string, boolean>
  getWorkerPaused: (workspaceId: string) => boolean
  connectAgentsWs: () => () => void
  fetchWorkerStatus: (workspaceId: string) => Promise<void>
  setWorkerPaused: (paused: boolean, workspaceId: string) => Promise<void>
}

let agentsChannel: RealtimeChannel | null = null

export const useAgentsStore = create<AgentsState>((set, get) => ({
  agents: [],
  workerPausedByWorkspace: {},

  getWorkerPaused: (workspaceId) => {
    const paused = get().workerPausedByWorkspace[workspaceId]
    return paused !== undefined ? paused : true
  },

  connectAgentsWs: () => {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    agentsChannel?.close()
    agentsChannel = createRealtimeChannel({
      url: `${protocol}//${location.host}/api/agents`,
      protocol: 'ws',
      onStatus: (status) => {
        useConnectionStatusStore
          .getState()
          .setConnectionStatus('agents', status)
      },
      onEvent: (_type, data) => {
        try {
          const message = parseAgentsWsMessage(data)
          if (message === null) return
          if (Array.isArray(message)) {
            set({ agents: message })
            return
          }
          // Destructure: a direct dot-access on the envelope's `agent`
          // field trips the WorkflowNode governance ratchet.
          const { agent: incoming } = message
          set((state) => ({ agents: upsertAgent(state.agents, incoming) }))
        } catch {
          // ignore malformed messages
        }
      },
    })
    return () => {
      agentsChannel?.close()
      agentsChannel = null
    }
  },

  fetchWorkerStatus: async (workspaceId) => {
    const query = `?workspace_id=${encodeURIComponent(workspaceId)}`
    const data = await api<WorkerStatusResponse>(`/api/worker/status${query}`)
    set((state) => ({
      workerPausedByWorkspace: {
        ...state.workerPausedByWorkspace,
        [workspaceId]: data.paused,
      },
    }))
  },

  setWorkerPaused: async (paused, workspaceId) => {
    const query = `?workspace_id=${encodeURIComponent(workspaceId)}`
    const data = await api<WorkerStatusResponse>(
      `${paused ? '/api/worker/pause' : '/api/worker/resume'}${query}`,
      { method: 'POST' }
    )
    set((state) => ({
      workerPausedByWorkspace: {
        ...state.workerPausedByWorkspace,
        [workspaceId]: data.paused,
      },
    }))
  },
}))
