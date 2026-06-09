import { create } from 'zustand'
import type { JobRecord } from '../types'
import { fetchJobs as apiFetchJobs, api } from '../api'

export type JobStatus = 'pending' | 'running' | 'completed' | 'failed'

interface JobState {
  jobs: JobRecord[]
  isLoading: boolean
  error: string | null
  selectedIds: Set<string>
  expandedId: string | null
  statusFilter: JobStatus | 'all'
  searchQuery: string

  fetchJobs: (workspaceId: string) => Promise<void>
  setStatusFilter: (filter: JobStatus | 'all') => void
  setSearchQuery: (query: string) => void
  toggleSelect: (id: string) => void
  selectAll: () => void
  clearSelection: () => void
  toggleExpand: (id: string) => void
  getFilteredJobs: () => JobRecord[]
  batchRerun: (workspaceId: string) => Promise<void>
  batchDelete: (workspaceId: string) => Promise<void>
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
    } catch (err) {
      const status =
        err && typeof err === 'object' && 'status' in err
          ? Number((err as { status?: unknown }).status)
          : undefined
      if (status === 404) {
        console.warn('Batch rerun endpoint is not implemented yet')
        set({ selectedIds: new Set() })
        return
      }
      const message = err instanceof Error ? err.message : 'Batch rerun failed'
      set({ error: message })
      throw err
    }
  },

  async batchDelete(workspaceId: string) {
    const ids = Array.from(get().selectedIds)
    if (ids.length === 0) return
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
    } catch (err) {
      const status =
        err && typeof err === 'object' && 'status' in err
          ? Number((err as { status?: unknown }).status)
          : undefined
      if (status === 404) {
        console.warn('Batch delete endpoint is not implemented yet')
        set({ selectedIds: new Set() })
        return
      }
      const message = err instanceof Error ? err.message : 'Batch delete failed'
      set({ error: message })
      throw err
    }
  },
}))
