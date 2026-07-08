import type { JobSummary } from '../../../jobTypes'
import type { JobState } from '../state'
import { normalizeJobs } from './fetchStateHelpers'
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
