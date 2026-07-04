import type { JobRecord, JobSummary } from '../types'

type WorkflowVersionJob = Pick<
  JobRecord | JobSummary,
  | 'workflow_version'
  | 'current_workflow_revision_version'
  | 'is_workflow_outdated'
>

export function jobWorkflowVersionText(job: WorkflowVersionJob): string | null {
  if (job.workflow_version == null) return null
  const current = job.current_workflow_revision_version
  if (job.is_workflow_outdated && current != null) {
    return `Workflow v${job.workflow_version} · 最新 v${current}`
  }
  return `Workflow v${job.workflow_version}`
}
