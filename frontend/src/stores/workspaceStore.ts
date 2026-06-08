import { create } from 'zustand'
import type { WorkspaceRecord, WorkspaceStats } from '../types'
import {
  fetchWorkspaces,
  createWorkspace as apiCreateWorkspace,
  updateWorkspace as apiUpdateWorkspace,
  deleteWorkspace as apiDeleteWorkspace,
  fetchWorkspaceStats,
} from '../api'

type WorkspaceState = {
  workspaces: WorkspaceRecord[]
  currentWorkspace: WorkspaceRecord | null
  workspaceStats: Record<string, WorkspaceStats>
  loading: boolean
  error: string | null

  fetchWorkspaces: () => Promise<void>
  createWorkspace: (name: string) => Promise<WorkspaceRecord>
  updateWorkspace: (
    id: string,
    fields: {
      name?: string
      default_pipeline_key?: string
      cms_config?: Record<string, unknown>
      resource_config?: Record<string, unknown>
    }
  ) => Promise<WorkspaceRecord>
  deleteWorkspace: (id: string) => Promise<void>
  fetchWorkspaceStats: (id: string) => Promise<void>
  setCurrentWorkspace: (w: WorkspaceRecord | null) => void
}

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  workspaces: [],
  currentWorkspace: null,
  workspaceStats: {},
  loading: false,
  error: null,

  async fetchWorkspaces() {
    set({ loading: true, error: null })
    try {
      const data = await fetchWorkspaces()
      set({ workspaces: data.workspaces, loading: false })
    } catch (err) {
      set({ error: String(err), loading: false })
    }
  },

  async createWorkspace(name: string) {
    try {
      const ws = await apiCreateWorkspace(name)
      set((s) => ({ workspaces: [...s.workspaces, ws], error: null }))
      return ws
    } catch (err) {
      set({ error: String(err) })
      throw err
    }
  },

  async updateWorkspace(id, fields) {
    try {
      const ws = await apiUpdateWorkspace(id, fields)
      set((s) => ({
        workspaces: s.workspaces.map((item) => (item.id === id ? ws : item)),
        currentWorkspace: s.currentWorkspace?.id === id ? ws : s.currentWorkspace,
        error: null,
      }))
      return ws
    } catch (err) {
      set({ error: String(err) })
      throw err
    }
  },

  async deleteWorkspace(id: string) {
    try {
      await apiDeleteWorkspace(id)
      set((s) => ({
        workspaces: s.workspaces.filter((w) => w.id !== id),
        error: null,
      }))
    } catch (err) {
      set({ error: String(err) })
      throw err
    }
  },

  async fetchWorkspaceStats(id: string) {
    try {
      const stats = await fetchWorkspaceStats(id)
      set((s) => ({
        workspaceStats: { ...s.workspaceStats, [id]: stats },
      }))
    } catch {
      // ignore — card will show fallback
    }
  },

  setCurrentWorkspace(w) {
    set({ currentWorkspace: w })
  },
}))
