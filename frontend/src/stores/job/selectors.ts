import type { JobState } from './state'
import { normalizeJobStatus } from './state'

export function getVisibleJobs(state: JobState) {
  const query = state.filterConfig.search.trim().toLowerCase()
  return state.jobs.filter((job) => {
    if (state.filterConfig.status !== 'all') {
      if (normalizeJobStatus(job.status) !== state.filterConfig.status) {
        return false
      }
    }
    if (query) {
      const source = (job.source_id ?? '').toLowerCase()
      const title = (job.title ?? '').toLowerCase()
      if (!source.includes(query) && !title.includes(query)) {
        return false
      }
    }
    return true
  })
}
