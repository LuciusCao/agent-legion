import { create } from 'zustand'
import type { JobRecord } from '../types'
import { fetchJobs as apiFetchJobs, api } from '../api'
import { useUiStore } from './uiStore'

export type JobStatus = 'pending' | 'running' | 'completed' | 'failed'

interface JobState {
  jobs: JobRecord[]
  isLoading: boolean
  error: string | null
  selectedIds: Set<string>
  expandedId: string | null
  statusFilter: JobStatus | 'all'
  searchQuery: string
  selectMode: boolean
  batchDeleteLoading: boolean
  batchPackageLoading: boolean

  fetchJobs: (workspaceId: string) => Promise<void>
  setStatusFilter: (filter: JobStatus | 'all') => void
  setSearchQuery: (query: string) => void
  toggleSelectMode: () => void
  toggleSelect: (id: string) => void
  selectAll: () => void
  selectFailed: () => void
  clearSelection: () => void
  toggleExpand: (id: string) => void
  getFilteredJobs: () => JobRecord[]
  batchRerun: (workspaceId: string) => Promise<void>
  batchDelete: (workspaceId: string) => Promise<void>
  batchPackage: (workspaceId: string) => Promise<void>
}

function normalizeJobStatus(status: string): JobStatus {
  switch (status) {
    case 'pending':
    case 'running':
    case 'completed':
    case 'failed':
      return status
    default:
      return 'pending'
  }
}

function getVisibleJobs(state: JobState): JobRecord[] {
  const query = state.searchQuery.trim().toLowerCase()
  return state.jobs.filter((job) => {
    if (state.statusFilter !== 'all') {
      if (normalizeJobStatus(job.status) !== state.statusFilter) {
        return false
      }
    }
    if (query) {
      const source = (job.source_id ?? '').toLowerCase()
      const title = (job.title ?? '').toLowerCase()
      if (!source.includes(query) && !title.includes(query)) {
        return false
      }
    }
    return true
  })
}

export const useJobStore = create<JobState>((set, get) => ({
  jobs: [],
  isLoading: false,
  error: null,
  selectedIds: new Set(),
  expandedId: null,
  statusFilter: 'all',
  searchQuery: '',
  selectMode: false,
  batchDeleteLoading: false,
  batchPackageLoading: false,

  async fetchJobs(workspaceId: string) {
    set({ isLoading: true, error: null })
    try {
      const data = await apiFetchJobs(workspaceId)
      set({ jobs: data.jobs, error: null, isLoading: false })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load jobs'
      set({ error: message, isLoading: false })
    }
  },

  setStatusFilter(filter) {
    set({ statusFilter: filter, selectedIds: new Set() })
  },

  setSearchQuery(query) {
    set({ searchQuery: query, selectedIds: new Set() })
  },

  toggleSelectMode() {
    set((state) => ({
      selectMode: !state.selectMode,
      selectedIds: new Set(),
    }))
  },

  toggleSelect(id: string) {
    set((state) => {
      const next = new Set(state.selectedIds)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return { selectedIds: next }
    })
  },

  selectAll() {
    set((state) => {
      const visible = getVisibleJobs(state)
      return { selectedIds: new Set(visible.map((j) => j.id)) }
    })
  },

  selectFailed() {
    set((state) => {
      const failedIds = state.jobs
        .filter((j) => normalizeJobStatus(j.status) === 'failed')
        .map((j) => j.id)
      return { selectedIds: new Set(failedIds) }
    })
  },

  clearSelection() {
    set({ selectedIds: new Set() })
  },

  toggleExpand(id: string) {
    set((state) => ({ expandedId: state.expandedId === id ? null : id }))
  },

  getFilteredJobs() {
    return getVisibleJobs(get())
  },

  async batchRerun(workspaceId: string) {
    const ids = Array.from(get().selectedIds)
    if (ids.length === 0) return
    try {
      await api(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch-rerun`,
        {
          method: 'POST',
          body: JSON.stringify({ job_ids: ids }),
        }
      )
      set({ selectedIds: new Set() })
      useUiStore
        .getState()
        .showToast(`成功重跑 ${ids.length} 个任务`, 'success')
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Batch rerun failed'
      set({ error: message })
      useUiStore.getState().showToast(message, 'error')
      throw err
    }
  },

  async batchDelete(workspaceId: string) {
    const ids = Array.from(get().selectedIds)
    if (ids.length === 0) return
    set({ batchDeleteLoading: true })
    try {
      await api(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/batch`,
        {
          method: 'DELETE',
          body: JSON.stringify({ job_ids: ids }),
        }
      )
      set((state) => ({
        jobs: state.jobs.filter((j) => !state.selectedIds.has(j.id)),
        selectedIds: new Set(),
      }))
      useUiStore
        .getState()
        .showToast(`成功删除 ${ids.length} 个任务`, 'success')
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Batch delete failed'
      set({ error: message })
      useUiStore.getState().showToast(message, 'error')
      throw err
    } finally {
      set({ batchDeleteLoading: false })
    }
  },

  async batchPackage(workspaceId: string) {
    const ids = Array.from(get().selectedIds)
    if (ids.length === 0) return
    const completedIds = ids.filter(
      (id) => get().jobs.find((j) => j.id === id)?.status === 'completed'
    )
    if (completedIds.length === 0) {
      useUiStore.getState().showToast('没有已完成的任务可打包', 'error')
      return
    }
    set({ batchPackageLoading: true })
    try {
      await api(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/package`,
        {
          method: 'POST',
          body: JSON.stringify({ job_ids: completedIds }),
        }
      )
      useUiStore
        .getState()
        .showToast(`已打包 ${completedIds.length} 个任务`, 'success')
      await get().fetchJobs(workspaceId)
      get().clearSelection()
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Batch package failed'
      set({ error: message })
      useUiStore.getState().showToast(message, 'error')
      throw err
    } finally {
      set({ batchPackageLoading: false })
    }
  },
}))
