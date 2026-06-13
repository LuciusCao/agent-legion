import { create } from 'zustand'
import type {
  BatchJobMutationResult,
  JobSummary,
  WorkspacePackageResult,
} from '../jobTypes'
import { fetchJobs as apiFetchJobs } from '../api'
import {
  batchRerunJobs,
  batchDeleteJobs,
  packageJobs,
  batchRunToJobs,
  continueJob as apiContinueJob,
} from '../jobApi'
import { useUiStore } from './uiStore'

export type JobStatus = 'pending' | 'running' | 'completed' | 'failed'

interface JobState {
  jobs: JobSummary[]
  isLoading: boolean
  error: string | null
  selectedIds: Set<string>
  expandedId: string | null
  statusFilter: JobStatus | 'all'
  searchQuery: string
  selectMode: boolean
  batchDeleteLoading: boolean
  batchPackageLoading: boolean
  batchRerunLoading: boolean
  batchRunToLoading: boolean
  continueLoading: boolean

  fetchJobs: (workspaceId: string) => Promise<void>
  setStatusFilter: (filter: JobStatus | 'all') => void
  setSearchQuery: (query: string) => void
  toggleSelectMode: () => void
  toggleSelect: (id: string) => void
  selectAll: () => void
  selectFailed: () => void
  clearSelection: () => void
  toggleExpand: (id: string) => void
  getFilteredJobs: () => JobSummary[]
  batchRerun: (
    workspaceId: string,
    nodeKey: string
  ) => Promise<BatchJobMutationResult>
  batchDelete: (workspaceId: string) => Promise<BatchJobMutationResult>
  batchPackage: (workspaceId: string) => Promise<WorkspacePackageResult>
  batchRunTo: (
    workspaceId: string,
    targetNodeKey: string,
    startNodeKey?: string
  ) => Promise<BatchJobMutationResult>
  continueJob: (jobId: string) => Promise<{
    job_id: string
    operation: string
    status: string
    message?: string | null
    node_key?: string | null
    reason_code?: string | null
  }>
}

type MutationCounts = {
  succeeded: number
  skipped: number
  failed: number
}

function countMutationResults(
  results: { status: 'succeeded' | 'skipped' | 'failed' }[]
): MutationCounts {
  return results.reduce(
    (acc, r) => {
      if (r.status === 'succeeded') acc.succeeded += 1
      else if (r.status === 'skipped') acc.skipped += 1
      else if (r.status === 'failed') acc.failed += 1
      return acc
    },
    { succeeded: 0, skipped: 0, failed: 0 }
  )
}

function makeMutationToast(action: string, counts: MutationCounts): string {
  if (counts.skipped === 0 && counts.failed === 0) {
    return `${action}完成：成功 ${counts.succeeded} 项`
  }
  if (counts.failed === 0) {
    return `${action}完成：成功 ${counts.succeeded} 项，跳过 ${counts.skipped} 项`
  }
  return `${action}完成：成功 ${counts.succeeded} 项，跳过 ${counts.skipped} 项，失败 ${counts.failed} 项`
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

function getVisibleJobs(state: JobState): JobSummary[] {
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
  batchRerunLoading: false,
  batchRunToLoading: false,
  continueLoading: false,

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

  async batchRerun(workspaceId: string, nodeKey: string) {
    const ids = Array.from(get().selectedIds)
    if (ids.length === 0) return { results: [] }
    set({ batchRerunLoading: true })
    try {
      const data = await batchRerunJobs(workspaceId, nodeKey, ids)
      const results = data.results ?? []
      const succeededIds = new Set(
        results.filter((r) => r.status === 'succeeded').map((r) => r.job_id)
      )
      set((state) => {
        const nextSelected = new Set(state.selectedIds)
        for (const id of succeededIds) {
          nextSelected.delete(id)
        }
        return {
          selectedIds: nextSelected,
          selectMode: nextSelected.size === 0 ? false : state.selectMode,
        }
      })
      const counts = countMutationResults(results)
      useUiStore
        .getState()
        .showToast(
          makeMutationToast('重跑', counts),
          counts.failed > 0 ? 'error' : 'success'
        )
      await get().fetchJobs(workspaceId)
      return data
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Batch rerun failed'
      set({ error: message })
      useUiStore.getState().showToast(message, 'error')
      throw err
    } finally {
      set({ batchRerunLoading: false })
    }
  },

  async batchDelete(workspaceId: string) {
    const ids = Array.from(get().selectedIds)
    if (ids.length === 0) return { results: [] }
    set({ batchDeleteLoading: true })
    try {
      const data = await batchDeleteJobs(workspaceId, ids)
      const results = data.results ?? []
      const succeededIds = new Set(
        results.filter((r) => r.status === 'succeeded').map((r) => r.job_id)
      )
      set((state) => {
        const nextSelected = new Set(state.selectedIds)
        const nextJobs = state.jobs.filter((j) => {
          if (succeededIds.has(j.id)) {
            nextSelected.delete(j.id)
            return false
          }
          return true
        })
        return {
          jobs: nextJobs,
          selectedIds: nextSelected,
          selectMode: nextSelected.size === 0 ? false : state.selectMode,
        }
      })
      const counts = countMutationResults(results)
      useUiStore
        .getState()
        .showToast(
          makeMutationToast('删除', counts),
          counts.failed > 0 ? 'error' : 'success'
        )
      return data
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
    if (ids.length === 0)
      return { results: [], succeeded_count: 0, failed_count: 0 }
    const completedIds = ids.filter(
      (id) => get().jobs.find((j) => j.id === id)?.status === 'completed'
    )
    if (completedIds.length === 0) {
      useUiStore.getState().showToast('没有已完成的任务可打包', 'error')
      return { results: [], succeeded_count: 0, failed_count: 0 }
    }
    set({ batchPackageLoading: true })
    try {
      const data = await packageJobs(workspaceId, completedIds)
      const results = data.results ?? []
      const succeededIds = new Set(
        results.filter((r) => r.status === 'succeeded').map((r) => r.job_id)
      )
      set((state) => {
        const nextSelected = new Set(state.selectedIds)
        for (const id of succeededIds) {
          nextSelected.delete(id)
        }
        return {
          selectedIds: nextSelected,
          selectMode: nextSelected.size === 0 ? false : state.selectMode,
        }
      })
      useUiStore
        .getState()
        .showToast(
          `打包完成：成功 ${data.succeeded_count} 项，失败 ${data.failed_count} 项`,
          data.failed_count > 0 ? 'error' : 'success'
        )
      await get().fetchJobs(workspaceId)
      return data
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

  async batchRunTo(
    workspaceId: string,
    targetNodeKey: string,
    startNodeKey?: string
  ) {
    const ids = Array.from(get().selectedIds)
    if (ids.length === 0) return { results: [] }
    set({ batchRunToLoading: true })
    try {
      const data = await batchRunToJobs(
        workspaceId,
        targetNodeKey,
        ids,
        startNodeKey
      )
      const results = data.results ?? []
      const succeededIds = new Set(
        results.filter((r) => r.status === 'succeeded').map((r) => r.job_id)
      )
      set((state) => {
        const nextSelected = new Set(state.selectedIds)
        for (const id of succeededIds) {
          nextSelected.delete(id)
        }
        return {
          selectedIds: nextSelected,
          selectMode: nextSelected.size === 0 ? false : state.selectMode,
        }
      })
      const counts = countMutationResults(results)
      useUiStore
        .getState()
        .showToast(
          makeMutationToast('运行到', counts),
          counts.failed > 0 ? 'error' : 'success'
        )
      await get().fetchJobs(workspaceId)
      return data
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Batch run-to failed'
      set({ error: message })
      useUiStore.getState().showToast(message, 'error')
      throw err
    } finally {
      set({ batchRunToLoading: false })
    }
  },

  async continueJob(jobId: string) {
    set({ continueLoading: true })
    try {
      const data = await apiContinueJob(jobId)
      useUiStore
        .getState()
        .showToast(
          data.status === 'succeeded' ? '继续完整流程成功' : '继续完整流程失败',
          data.status === 'succeeded' ? 'success' : 'error'
        )
      return data
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Continue full flow failed'
      set({ error: message })
      useUiStore.getState().showToast(message, 'error')
      throw err
    } finally {
      set({ continueLoading: false })
    }
  },
}))
