import { create } from 'zustand'
import { api } from '../api'
import { createRealtimeChannel, type RealtimeChannel } from '../lib/realtime'
import { parseAgentsWsMessage, upsertAgent } from './agentsWsMessages'
import { useExecutorsStore } from './executorsStore'
import type { AgentStatus, ContentType, WorkerStatusResponse } from '../types'

interface Toast {
  message: string
  type: 'success' | 'error'
}

export interface UiState {
  agents: AgentStatus[]
  addDialogOpen: boolean
  addContentType: ContentType
  addDialogContext: 'video' | 'workspace'
  addDialogWorkspaceId: string | undefined
  workspacePackageDialogOpen: boolean
  tokenUsageDialogOpen: boolean
  workerPausedByWorkspace: Record<string, boolean>
  toast: Toast | null
  getWorkerPaused: (workspaceId: string) => boolean
  connectAgentsWs: () => () => void
  fetchWorkerStatus: (workspaceId: string) => Promise<void>
  setWorkerPaused: (paused: boolean, workspaceId: string) => Promise<void>
  openAddDialog: (opts?: {
    context?: 'video' | 'workspace'
    workspaceId?: string
  }) => void
  closeAddDialog: () => void
  setAddContentType: (type: ContentType) => void
  setWorkspacePackageDialogOpen: (open: boolean) => void
  setTokenUsageDialogOpen: (open: boolean) => void
  showToast: (message: string, type: 'success' | 'error') => void
  clearToast: () => void
}

let agentsChannel: RealtimeChannel | null = null

export const useUiStore = create<UiState>((set, get) => ({
  agents: [],
  addDialogOpen: false,
  addContentType: 'knowledge',
  addDialogContext: 'workspace',
  addDialogWorkspaceId: undefined,
  workspacePackageDialogOpen: false,
  tokenUsageDialogOpen: false,
  workerPausedByWorkspace: {},
  toast: null,

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
        useExecutorsStore.getState().setConnectionStatus('agents', status)
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

  openAddDialog: (opts) =>
    set({
      addDialogOpen: true,
      addContentType: 'knowledge',
      addDialogContext: opts?.context || 'workspace',
      addDialogWorkspaceId: opts?.workspaceId,
    }),
  closeAddDialog: () => set({ addDialogOpen: false }),
  setAddContentType: (type) => set({ addContentType: type }),
  setWorkspacePackageDialogOpen: (open) =>
    set({ workspacePackageDialogOpen: open }),
  setTokenUsageDialogOpen: (open) => set({ tokenUsageDialogOpen: open }),
  showToast: (message, type) => set({ toast: { message, type } }),
  clearToast: () => set({ toast: null }),
}))
