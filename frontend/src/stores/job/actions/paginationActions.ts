import { fetchJobsSnapshot } from '../../../api'
import type { JobFacetsResponse, JobSummary } from '../../../types/jobTypes'
import { toJobListFilterParams } from '../listFilterParams'
import type { JobState, JobStoreSet } from '../state'
import { appendJobsSnapshotUpdate } from './appendActions'
import {
  createRefreshFirstPage,
  PAGE_SIZE,
  setJobsPageUpdate,
} from './refreshFirstPage'

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

// Generation counters invalidate in-flight loads when a newer list load
// (filter refetch, workspace switch via jobsWorkspaceId guard) supersedes
// them, so stale pages never append to or replace the current list. The
// refresh generation lives in refreshFirstPage.ts; it cancels in-flight
// appends through the cancelLoadMore callback.
let loadMoreGeneration = 0

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

    refreshFirstPage: createRefreshFirstPage(set, get, () => {
      loadMoreGeneration += 1
    }),
  }
}
