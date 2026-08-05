import { fetchJobs as apiFetchJobs } from '../../../api'
import type { JobSummary } from '../../../types/jobTypes'
import {
  computeFilterCounts,
  computeFilteredJobIds,
} from '../filterLogic/incrementalFilters'
import { createOptionAccumulator } from '../filterLogic/optionAccumulator'
import type { JobState, JobStoreSet } from '../state'
import { clearedSelectionState } from './selectionModeState'
import { filtersForWorkspace } from './workspaceFilterState'

export const normalizeJobs = (jobs: JobSummary[]) => ({
  jobs,
  jobsById: Object.fromEntries(jobs.map((job) => [job.id, job])),
  jobIds: jobs.map((job) => job.id),
  jobIndexById: Object.fromEntries(jobs.map((job, index) => [job.id, index])),
  optionAccumulator: createOptionAccumulator(jobs),
})

export function isCurrentWorkspace(
  state: JobState,
  workspaceId: string
): boolean {
  return (
    state.jobsWorkspaceId === workspaceId ||
    (state.jobIds.length > 0 &&
      state.jobIds.every(
        (id) => state.jobsById[id]?.workspace_id === workspaceId
      ))
  )
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

export const failJobFetch =
  (ws: string, msg: string) =>
  (state: JobState): Partial<JobState> =>
    state.jobsWorkspaceId === ws
      ? {
          error: msg,
          isLoading: false,
          jobs: [],
          jobsById: {},
          jobIds: [],
          jobIndexById: {},
          optionAccumulator: state.optionAccumulator,
          filteredJobIds: [],
          filterCounts: computeFilterCounts([], {}, state.filterConfig),
        }
      : {}

export const finishJobFetch =
  (ws: string, jobs: JobSummary[]) =>
  (state: JobState): Partial<JobState> => {
    if (state.jobsWorkspaceId !== ws) return {}
    const { jobsById, jobIds, jobIndexById, optionAccumulator } =
      normalizeJobs(jobs)
    const filterConfig = state.filterConfig
    return {
      jobs,
      jobsById,
      jobIds,
      jobIndexById,
      optionAccumulator,
      filteredJobIds: computeFilteredJobIds(jobIds, jobsById, filterConfig),
      filterCounts: computeFilterCounts(jobIds, jobsById, filterConfig),
      error: null,
      isLoading: false,
    }
  }

export const resetForWorkspace =
  (ws: string) =>
  (state: JobState): Partial<JobState> => {
    const keep = isCurrentWorkspace(state, ws)
    const filterConfig = filtersForWorkspace(state, ws)
    return {
      ...resetJobListForFilterChange({ ...state, filterConfig }),
      jobsWorkspaceId: ws,
      ...(keep ? { selectedIds: state.selectedIds } : clearedSelectionState()),
      filterConfig,
    }
  }

export const startJobFetch = resetForWorkspace

const inflight = new Map<string, Promise<{ jobs: JobSummary[] }>>()

export function getOrStartFetch(
  workspaceId: string
): Promise<{ jobs: JobSummary[] }> {
  let p = inflight.get(workspaceId)
  if (!p) {
    p = apiFetchJobs(workspaceId).finally(() => inflight.delete(workspaceId))
    inflight.set(workspaceId, p)
  }
  return p
}

export function fetchActions(set: JobStoreSet) {
  return {
    async fetchJobs(workspaceId: string) {
      set(startJobFetch(workspaceId))
      try {
        const data = await getOrStartFetch(workspaceId)
        set(finishJobFetch(workspaceId, data.jobs))
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to load jobs'
        set(failJobFetch(workspaceId, msg))
      }
    },
    resetForWorkspace: (workspaceId: string) =>
      set(resetForWorkspace(workspaceId)),
    setJobsAndFinishLoading: (jobs: JobSummary[]) =>
      set((state) => ({
        ...finishJobFetch(state.jobsWorkspaceId || '', jobs)(state),
        revision: state.revision,
      })),
    failJobFetch: (workspaceId: string, message: string) =>
      set(failJobFetch(workspaceId, message)),
  }
}
