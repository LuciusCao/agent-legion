import { create } from 'zustand'
import { api } from '../api'
import type { AgentStatus, ContentType } from '../types'

interface Toast {
  message: string
  type: 'success' | 'error'
}

interface UiState {
  agents: AgentStatus[]
  addDialogOpen: boolean
  addContentType: ContentType
  addDialogContext: 'video' | 'workspace'
  addDialogWorkspaceId: string | undefined
  rerunDialogOpen: boolean
  deleteDialogOpen: boolean
  workerPaused: boolean
  toast: Toast | null
  pageTitle: string | null
  connectAgentsWs: () => () => void
  fetchWorkerStatus: () => Promise<void>
  setWorkerPaused: (paused: boolean) => Promise<void>
  openAddDialog: (opts?: {
    context?: 'video' | 'workspace'
    workspaceId?: string
  }) => void
  closeAddDialog: () => void
  setAddContentType: (type: ContentType) => void
  openRerunDialog: () => void
  closeRerunDialog: () => void
  openDeleteDialog: () => void
  closeDeleteDialog: () => void
  showToast: (message: string, type: 'success' | 'error') => void
  clearToast: () => void
  setPageTitle: (title: string | null) => void
}

let wsInstance: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null

export const useUiStore = create<UiState>((set) => ({
  agents: [],
  addDialogOpen: false,
  addContentType: 'knowledge',
  addDialogContext: 'video',
  addDialogWorkspaceId: undefined,
  rerunDialogOpen: false,
  deleteDialogOpen: false,
  workerPaused: true,
  toast: null,
  pageTitle: null,

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

  fetchWorkerStatus: async () => {
    const data = await api<{ paused: boolean }>('/api/worker/status')
    set({ workerPaused: data.paused })
  },

  setWorkerPaused: async (paused) => {
    const data = await api<{ paused: boolean }>(
      paused ? '/api/worker/pause' : '/api/worker/resume',
      { method: 'POST' }
    )
    set({ workerPaused: data.paused })
  },

  openAddDialog: (opts) =>
    set({
      addDialogOpen: true,
      addContentType: 'knowledge',
      addDialogContext: opts?.context || 'video',
      addDialogWorkspaceId: opts?.workspaceId,
    }),
  closeAddDialog: () => set({ addDialogOpen: false }),
  setAddContentType: (type) => set({ addContentType: type }),
  openRerunDialog: () => set({ rerunDialogOpen: true }),
  closeRerunDialog: () => set({ rerunDialogOpen: false }),
  openDeleteDialog: () => set({ deleteDialogOpen: true }),
  closeDeleteDialog: () => set({ deleteDialogOpen: false }),
  showToast: (message, type) => set({ toast: { message, type } }),
  clearToast: () => set({ toast: null }),
  setPageTitle: (title) => set({ pageTitle: title }),
}))
