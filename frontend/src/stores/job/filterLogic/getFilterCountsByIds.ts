import type { JobFilterConfig } from '../state'
import type { JobSummary } from '../../../jobTypes'
import { countJobs } from './filterCountsCore'

export function getFilterCountsByIds(
  jobIds: string[],
  jobsById: Record<string, JobSummary>,
  filterConfig: JobFilterConfig
): ReturnType<typeof countJobs> {
  function* iterateJobs(): Generator<JobSummary> {
    for (const id of jobIds) {
      const job = jobsById[id]
      if (job) yield job
    }
  }
  return countJobs(iterateJobs(), filterConfig)
}
