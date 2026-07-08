import type { JobSummary } from '../../../jobTypes'
import type { JobState } from '../state'
import { normalizeJobs } from './fetchStateHelpers'
import {
  computeFilterCounts,
  computeFilteredJobIds,
} from '../filterLogic/incrementalFilters'
export { failJobFetch } from './fetchFailure'

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
