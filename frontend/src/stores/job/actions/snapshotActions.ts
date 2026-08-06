import type { JobSummary } from '../../../types/jobTypes'
import type { JobState, JobStoreSet } from '../state'
import { normalizeJobs } from './fetch'
import {
  computeFilterCounts,
  computeFilteredJobIds,
} from '../filterLogic/incrementalFilters'

export function setJobsSnapshotUpdate(
  state: JobState,
  workspaceId: string,
  revision: number,
  jobs: JobSummary[]
): Partial<JobState> {
  if (
    (state.jobsWorkspaceId && state.jobsWorkspaceId !== workspaceId) ||
    revision < state.revision
  )
    return {}
  const { jobsById, jobIds, jobIndexById, optionAccumulator } =
    normalizeJobs(jobs)
  return {
    jobs,
    jobsById,
    jobIds,
    jobIndexById,
    optionAccumulator,
    filteredJobIds: computeFilteredJobIds(jobIds, jobsById, state.filterConfig),
    filterCounts: computeFilterCounts(jobIds, jobsById, state.filterConfig),
    jobsWorkspaceId: workspaceId,
    revision,
    isLoading: false,
    error: null,
  }
}

export function snapshotActions(set: JobStoreSet) {
  return {
    setJobsSnapshot: (
      workspaceId: string,
      revision: number,
      jobs: JobSummary[]
    ) =>
      set((state) => setJobsSnapshotUpdate(state, workspaceId, revision, jobs)),
  }
}
