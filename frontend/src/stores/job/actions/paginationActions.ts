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
  // #1183：水位与内容绑定——快照绝不以高于其内容的 revision 落地（服务端
  // 采样 revision 先于读 jobs；抬水位会让采样后产生的 patch 被 revision
  // 守卫永久丢弃）。在途期间落地了更高 revision patch 的陈旧快照由
  // setJobsSnapshotUpdate 的守卫丢弃：SSE 路径由 pendingEvents 排队覆盖，
  // refreshFirstPage 路径由调用方原地重拉覆盖（#1189 codex P1-a）。
  const base = setJobsSnapshotUpdate(state, workspaceId, revision, jobs)
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
// refreshFirstPage 的重拉收敛上限：响应 revision 低于 store 现水位即在途
// 期间有 patch 落地、内容落后，不应用、直接重拉（#1189 codex P1-a）。
const MAX_REFRESH_ATTEMPTS = 3

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
      // 只有 fetchJobsSnapshot 失败才置 listLoadError（整页错误）；facets
      // 只是计数面板，失败独立降级、绝不动已写入的列表（#1189 codex P1-b）。
      for (let attempt = 0; attempt < MAX_REFRESH_ATTEMPTS; attempt += 1) {
        let page: Awaited<ReturnType<typeof fetchJobsSnapshot>>
        try {
          page = await fetchJobsSnapshot(
            workspaceId,
            PAGE_SIZE,
            undefined,
            params
          )
        } catch (err) {
          if (!isCurrent()) return
          const message =
            err instanceof Error ? err.message : 'Failed to load jobs'
          set({ isLoading: false, listLoadError: message })
          return
        }
        if (!isCurrent() || get().filterConfig !== filterConfig) return
        // 在 set 内原子比较水位：落后则不应用、直接重拉。
        let applied = false
        set((state) => {
          if (page.revision < state.revision) return {}
          applied = true
          return setJobsPageUpdate(
            state,
            workspaceId,
            page.revision,
            page.jobs,
            page.total,
            page.next_cursor
          )
        })
        if (!applied) continue
        const facets = await fetchJobFacets(workspaceId, params).catch(
          () => null
        )
        if (!facets || !isCurrent() || get().filterConfig !== filterConfig) {
          return
        }
        set({ facets })
        return
      }
      // 三重竞争仍落后（罕见）：保留现有列表、patch 继续流动，仅收 loading。
      if (isCurrent()) set({ isLoading: false })
    },
  }
}
