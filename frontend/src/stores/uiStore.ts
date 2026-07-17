import { create } from 'zustand'
import { api } from '../api'
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

let wsInstance: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null

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
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (wsInstance) {
      wsInstance.onclose = null
      wsInstance.close()
    }
    wsInstance = new WebSocket(`${protocol}//${location.host}/api/agents`)
    wsInstance.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as AgentStatus[]
        set({ agents: data })
      } catch {
        // ignore
      }
    }
    wsInstance.onclose = () => {
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null
        const { connectAgentsWs } = useUiStore.getState()
        connectAgentsWs()
      }, 3000)
    }
    return () => {
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      if (wsInstance) {
        wsInstance.onclose = null
        wsInstance.close()
        wsInstance = null
      }
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
