import type { JobSummary } from '../types'

export function canRerunJob(status: string): boolean {
  return status !== 'running'
}

export function canPackageJob(job: JobSummary): boolean {
  return job.status === 'completed' && !job.packed
}

export function canDeleteJob(): boolean {
  return true
}

export function canContinueJob(job: JobSummary): boolean {
  return (
    job.status === 'paused' &&
    job.execution_control?.pause_reason === 'target_reached'
  )
}

export type JobActionDisabled = {
  rerun: boolean
  runTo: boolean
  continue: boolean
  package: boolean
  clearPacked: boolean
  delete: boolean
}

export function computeActionDisabled(
  jobs: JobSummary[],
  loading: boolean
): JobActionDisabled {
  const noJobs = jobs.length === 0
  const noneRunnable = jobs.every((job) => !canRerunJob(job.status))
  return {
    rerun: noJobs || loading || noneRunnable,
    runTo: noJobs || loading || noneRunnable,
    continue: noJobs || loading || !jobs.some((job) => canContinueJob(job)),
    package: noJobs || loading || jobs.every((job) => !canPackageJob(job)),
    clearPacked: noJobs || loading || jobs.every((job) => !job.packed),
    delete: noJobs || loading,
  }
}
