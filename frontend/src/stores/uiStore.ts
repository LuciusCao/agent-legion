import { create } from 'zustand'
import { api } from '../api'
import type { AgentStatus, ContentType } from '../types'

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
  rerunDialogOpen: boolean
  deleteDialogOpen: boolean
  workspacePackageDialogOpen: boolean
  workerPaused: boolean
  workerPausedByWorkspace: Record<string, boolean>
  toast: Toast | null
  getWorkerPaused: (workspaceId?: string) => boolean
  connectAgentsWs: () => () => void
  fetchWorkerStatus: (workspaceId?: string) => Promise<void>
  setWorkerPaused: (paused: boolean, workspaceId?: string) => Promise<void>
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
  setWorkspacePackageDialogOpen: (open: boolean) => void
  showToast: (message: string, type: 'success' | 'error') => void
  clearToast: () => void
}

let wsInstance: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
const workerStatusKey = (workspaceId?: string) => workspaceId || 'video-hive'

export const useUiStore = create<UiState>((set, get) => ({
  agents: [],
  addDialogOpen: false,
  addContentType: 'knowledge',
  addDialogContext: 'video',
  addDialogWorkspaceId: undefined,
  rerunDialogOpen: false,
  deleteDialogOpen: false,
  workspacePackageDialogOpen: false,
  workerPaused: true,
  workerPausedByWorkspace: {},
  toast: null,

  getWorkerPaused: (workspaceId) => {
    const key = workerStatusKey(workspaceId)
    const paused = get().workerPausedByWorkspace[key]
    if (paused !== undefined) return paused
    return key === 'video-hive' ? get().workerPaused : true
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
    const query = workspaceId
      ? `?workspace_id=${encodeURIComponent(workspaceId)}`
      : ''
    const data = await api<{ paused: boolean }>(`/api/worker/status${query}`)
    const key = workerStatusKey(workspaceId)
    set((state) => ({
      workerPaused: key === 'video-hive' ? data.paused : state.workerPaused,
      workerPausedByWorkspace: {
        ...state.workerPausedByWorkspace,
        [key]: data.paused,
      },
    }))
  },

  setWorkerPaused: async (paused, workspaceId) => {
    const query = workspaceId
      ? `?workspace_id=${encodeURIComponent(workspaceId)}`
      : ''
    const data = await api<{ paused: boolean }>(
      `${paused ? '/api/worker/pause' : '/api/worker/resume'}${query}`,
      { method: 'POST' }
    )
    const key = workerStatusKey(workspaceId)
    set((state) => ({
      workerPaused: key === 'video-hive' ? data.paused : state.workerPaused,
      workerPausedByWorkspace: {
        ...state.workerPausedByWorkspace,
        [key]: data.paused,
      },
    }))
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
  setWorkspacePackageDialogOpen: (open) =>
    set({ workspacePackageDialogOpen: open }),
  showToast: (message, type) => set({ toast: { message, type } }),
  clearToast: () => set({ toast: null }),
}))
