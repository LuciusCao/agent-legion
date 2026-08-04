import type { ReactNode } from 'react'
import type { JobDetail } from '../../types/jobTypes'
import { WorkflowVersionChip } from '../../components/WorkflowVersionChip'

export function pageSubtitle(job: JobDetail['job']): ReactNode | null {
  const sourceId = job.source_id || null
  const versionChip =
    job.workflow_version != null ? <WorkflowVersionChip job={job} /> : null

  if (!sourceId && !versionChip) return null

  return (
    <>
      {sourceId}
      {sourceId && versionChip ? ' · ' : null}
      {versionChip}
    </>
  )
}
