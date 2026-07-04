import type { ReactNode } from 'react'
import type { JobDetailResponse } from '../../types'
import { WorkflowVersionChip } from '../../components/WorkflowVersionChip'

export function pageSubtitle(job: JobDetailResponse['job']): ReactNode | null {
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
