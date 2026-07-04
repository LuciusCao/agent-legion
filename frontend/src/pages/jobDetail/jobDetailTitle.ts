import type { JobDetailResponse } from '../../types'
import { jobWorkflowVersionText } from '../../lib/jobWorkflowVersion'

export function pageSubtitle(job: JobDetailResponse['job']): string | null {
  return (
    [job.source_id || null, jobWorkflowVersionText(job)]
      .filter(Boolean)
      .join(' · ') || null
  )
}
