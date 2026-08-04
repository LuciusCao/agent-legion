import type { JobSummary } from '../types'

export function canUpgradeJob(job: JobSummary): boolean {
  return job.is_workflow_outdated === true && job.status !== 'running'
}
