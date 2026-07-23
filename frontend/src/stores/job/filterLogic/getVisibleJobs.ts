import type { JobState } from '../state'
import type { JobSummary } from '../../../types/jobTypes'
import { passesFilters } from './passesFilters'

export function getVisibleJobs(state: JobState): JobSummary[] {
  const jobs: JobSummary[] = []
  for (const id of state.jobIds) {
    const job = state.jobsById[id]
    if (job && passesFilters(job, state.filterConfig)) jobs.push(job)
  }
  return jobs
}
