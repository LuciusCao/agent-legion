import type { JobState } from './state'
import { normalizeJobStatus } from './state'

export function getVisibleJobs(state: JobState) {
  const query = state.searchQuery.trim().toLowerCase()
  return state.jobs.filter((job) => {
    if (state.statusFilter !== 'all') {
      if (normalizeJobStatus(job.status) !== state.statusFilter) {
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
