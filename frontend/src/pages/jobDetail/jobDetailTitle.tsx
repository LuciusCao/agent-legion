import type { ReactNode } from 'react'
import type { JobDetail } from '../../types/jobTypes'
import { WorkflowVersionChip } from '../../components/WorkflowVersionChip'
import { JobClientTokenSource } from '../../components/job/JobClientTokenSource'

export function pageSubtitle(job: JobDetail['job']): ReactNode | null {
  // #925：带 client_token 的 job 以「来源材料 + client_token」替代裸
  // source_id（其 `~token` 后缀难以理解）；无 token 展示不变。
  const sourceId = job.client_token ? (
    <JobClientTokenSource job={job} />
  ) : (
    job.source_id || null
  )
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
