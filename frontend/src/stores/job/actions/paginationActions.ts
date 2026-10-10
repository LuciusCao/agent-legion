import { fetchJobFacets, fetchJobsSnapshot } from '../../../api'
import type { JobFacetsResponse, JobSummary } from '../../../types/jobTypes'
import { toJobListFilterParams } from '../listFilterParams'
import type { JobState, JobStoreSet } from '../state'
import { appendJobsSnapshotUpdate } from './appendActions'
import { resetJobListForFilterChange } from './fetch'
import { setJobsSnapshotUpdate } from './snapshotActions'

export function setJobsPageUpdate(
  state: JobState,
  workspaceId: string,
  revision: number,
  jobs: JobSummary[],
  total: number | null | undefined,
  nextCursor: string | null | undefined
): Partial<JobState> {
  // #1183：快照在途期间可能有更高 revision 的 patch 已落地（或上一个
  // workspace 残留的 revision 未清零）。单调取 max 让快照响应总是能整页
  // 替换列表、不被 revision 守卫静默丢弃——此前只有 refreshFirstPage 在
  // 调用点各自为政地取 max，SSE 快照路径（loadWorkspaceJobsSnapshot）漏
  // 掉，跨 workspace 切换后快照会被残留 revision 丢弃、页面卡 skeleton。
  const base = setJobsSnapshotUpdate(
    state,
    workspaceId,
    Math.max(revision, state.revision),
    jobs
  )
  if (Object.keys(base).length === 0) return {}
  return {
    ...base,
    nextCursor: nextCursor ?? null,
    hasMore: Boolean(nextCursor),
    totalJobs: total ?? null,
    loadingMore: false,
  }
}

export function appendJobsPageUpdate(
  state: JobState,
  workspaceId: string,
  jobs: JobSummary[],
  nextCursor: string | null | undefined
): Partial<JobState> {
  return {
    ...appendJobsSnapshotUpdate(state, workspaceId, jobs),
    nextCursor: nextCursor ?? null,
    hasMore: Boolean(nextCursor),
    loadingMore: false,
  }
}

const PAGE_SIZE = 500

// Generation counters invalidate in-flight loads when a newer list load
// (filter refetch, workspace switch via jobsWorkspaceId guard) supersedes
// them, so stale pages never append to or replace the current list.
let loadMoreGeneration = 0
let refreshGeneration = 0

export function paginationActions(set: JobStoreSet, get: () => JobState) {
  return {
    setJobsPage: (
      workspaceId: string,
      revision: number,
      jobs: JobSummary[],
      total: number | null | undefined,
      nextCursor: string | null | undefined
    ) =>
      set((state) =>
        setJobsPageUpdate(state, workspaceId, revision, jobs, total, nextCursor)
      ),

    setFacets: (workspaceId: string, facets: JobFacetsResponse) =>
      set((state) => (state.jobsWorkspaceId === workspaceId ? { facets } : {})),

    async loadMoreJobs(workspaceId: string) {
      const state = get()
      if (state.jobsWorkspaceId !== workspaceId) return
      if (!state.hasMore || state.loadingMore || !state.nextCursor) return
      const cursor = state.nextCursor
      const params = toJobListFilterParams(state.filterConfig)
      const generation = ++loadMoreGeneration
      set({ loadingMore: true })
      try {
        const page = await fetchJobsSnapshot(
          workspaceId,
          PAGE_SIZE,
          cursor,
          params
        )
        set((current) =>
          generation === loadMoreGeneration &&
          current.jobsWorkspaceId === workspaceId &&
          current.nextCursor === cursor
            ? appendJobsPageUpdate(
                current,
                workspaceId,
                page.jobs,
                page.next_cursor
              )
            : {}
        )
      } catch {
        if (generation === loadMoreGeneration) set({ loadingMore: false })
      }
    },

    async refreshFirstPage(workspaceId: string) {
      if (get().jobsWorkspaceId !== workspaceId) return
      const generation = ++refreshGeneration
      // Cancel any in-flight page append; the list is about to be replaced.
      loadMoreGeneration += 1
      const isCurrent = () =>
        generation === refreshGeneration &&
        get().jobsWorkspaceId === workspaceId
      set((state) => resetJobListForFilterChange(state))
      const filterConfig = get().filterConfig
      const params = toJobListFilterParams(filterConfig)
      try {
        const page = await fetchJobsSnapshot(
          workspaceId,
          PAGE_SIZE,
          undefined,
          params
        )
        if (!isCurrent() || get().filterConfig !== filterConfig) return
        set((state) =>
          setJobsPageUpdate(
            state,
            workspaceId,
            page.revision,
            page.jobs,
            page.total,
            page.next_cursor
          )
        )
        const facets = await fetchJobFacets(workspaceId, params)
        if (!isCurrent() || get().filterConfig !== filterConfig) return
        set({ facets })
      } catch (err) {
        if (!isCurrent()) return
        const message =
          err instanceof Error ? err.message : 'Failed to load jobs'
        set({ isLoading: false, error: message })
      }
    },
  }
}
