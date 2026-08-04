import type { JobState } from '../state'
import { countJobs } from './filterCountsCore'

export function getFilterCounts(state: JobState): ReturnType<typeof countJobs> {
  function* iterateJobs() {
    for (const id of state.jobIds) {
      const job = state.jobsById[id]
      if (job) yield job
    }
  }
  return countJobs(iterateJobs(), state.filterConfig)
}
