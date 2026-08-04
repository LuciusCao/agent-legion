import type { JobSummary } from '../../../types/jobTypes'
import type { JobState } from '../state'
import { computeFilterCounts } from '../filterLogic/incrementalFilters'
import { appendJobsSnapshotUpdate } from './appendState'
import { setJobsSnapshotUpdate } from './snapshotState'

export function setJobsPageUpdate(
  state: JobState,
  workspaceId: string,
  revision: number,
  jobs: JobSummary[],
  total: number | null | undefined,
  nextCursor: string | null | undefined
): Partial<JobState> {
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

export function resetJobListForFilterChange(
  state: JobState
): Partial<JobState> {
  return {
    jobs: [],
    jobsById: {},
    jobIds: [],
    jobIndexById: {},
    filteredJobIds: [],
    filterCounts: computeFilterCounts([], {}, state.filterConfig),
    facets: null,
    nextCursor: null,
    hasMore: false,
    totalJobs: null,
    loadingMore: false,
    isLoading: true,
    error: null,
  }
}
