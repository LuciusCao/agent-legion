import type { JobSummary } from '../../../jobTypes'

export function matchesSearch(job: JobSummary, query: string): boolean {
  if (!query) return true
  const q = query.trim().toLowerCase()
  const hay =
    `${job.id} ${job.source_id} ${job.batch_id} ${job.title}`.toLowerCase()
  return hay.includes(q)
}
