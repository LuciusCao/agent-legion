import type { JobState } from '../state'
import type { JobSummary } from '../../../jobTypes'
import { passesFilters } from './passesFilters'

export function getVisibleJobs(state: JobState): JobSummary[] {
  return state.jobs.filter((job) => passesFilters(job, state.filterConfig))
}
