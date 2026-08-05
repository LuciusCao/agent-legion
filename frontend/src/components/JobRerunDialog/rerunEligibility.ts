import { normalizeJobStatus } from '../../stores/jobStore'
import type { JobSummary } from '../../types'

const RERUNNABLE_NODE_STATUSES = new Set(['completed', 'failed'])

export function partitionJobsForNodeRerun(
  jobs: JobSummary[],
  nodeKey: string,
  excludedJobs: JobSummary[]
) {
  const excludedIds = new Set(excludedJobs.map((job) => job.id))
  const runnableJobs: JobSummary[] = []
  const notStartedJobs: JobSummary[] = []
  const runningJobs: JobSummary[] = []

  for (const job of jobs) {
    if (excludedIds.has(job.id)) continue
    if (normalizeJobStatus(job.status) === 'running') {
      runningJobs.push(job)
      continue
    }
    const node = job.node_summaries?.find(
      (summary) => summary.node_key === nodeKey
    )
    if (node && RERUNNABLE_NODE_STATUSES.has(node.status)) {
      runnableJobs.push(job)
    } else {
      notStartedJobs.push(job)
    }
  }

  return { runnableJobs, notStartedJobs, runningJobs }
}
