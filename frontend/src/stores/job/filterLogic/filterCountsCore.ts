import type { JobFilterConfig } from '../state'
import { normalizeJobStatus } from '../state'
import type { JobSummary } from '../../../types/jobTypes'
import type { FilterCounts } from './types'
import { passesFilters } from './passesFilters'

export function countJobs(
  jobs: Iterable<JobSummary>,
  filterConfig: JobFilterConfig
): FilterCounts {
  const counts: FilterCounts = {
    status: {},
    workflowVersion: {},
    activeNodeKey: {},
  }
  let statusAll = 0
  let workflowVersionAll = 0
  let activeNodeKeyAll = 0
  for (const job of jobs) {
    if (passesFilters(job, filterConfig, 'status')) {
      statusAll++
      const key = normalizeJobStatus(job.status)
      counts.status[key] = (counts.status[key] ?? 0) + 1
    }
    if (passesFilters(job, filterConfig, 'workflowVersion')) {
      workflowVersionAll++
      const version = job.workflow_version
      if (version !== null && version !== undefined) {
        counts.workflowVersion[String(version)] =
          (counts.workflowVersion[String(version)] ?? 0) + 1
      } else {
        counts.workflowVersion.none = (counts.workflowVersion.none ?? 0) + 1
      }
    }
    if (passesFilters(job, filterConfig, 'activeNodeKey')) {
      activeNodeKeyAll++
      const key = job.active_node_key
      if (key) {
        counts.activeNodeKey[key] = (counts.activeNodeKey[key] ?? 0) + 1
      }
    }
  }

  counts.status.all = statusAll
  counts.workflowVersion.all = workflowVersionAll
  counts.activeNodeKey.all = activeNodeKeyAll
  return counts
}
