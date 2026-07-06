import type { JobFilterConfig } from '../state'
import { normalizeJobStatus } from '../state'
import type { JobSummary } from '../../../jobTypes'
import type { FilterCounts } from './types'
import { passesFilters } from './passesFilters'

export function getFilterCounts(source: {
  jobs: JobSummary[]
  filterConfig: JobFilterConfig
}): FilterCounts {
  const { jobs, filterConfig } = source
  const counts: FilterCounts = {
    status: {},
    workflowVersion: {},
    activeNodeKey: {},
  }

  for (const job of jobs) {
    if (passesFilters(job, filterConfig, 'status')) {
      const key = normalizeJobStatus(job.status)
      counts.status[key] = (counts.status[key] ?? 0) + 1
    }
    if (passesFilters(job, filterConfig, 'workflowVersion')) {
      const version = job.workflow_version
      if (version !== null && version !== undefined) {
        counts.workflowVersion[String(version)] =
          (counts.workflowVersion[String(version)] ?? 0) + 1
      }
    }
    if (passesFilters(job, filterConfig, 'activeNodeKey')) {
      const key = job.active_node_key
      if (key) {
        counts.activeNodeKey[key] = (counts.activeNodeKey[key] ?? 0) + 1
      }
    }
  }

  counts.status.all = Object.values(counts.status).reduce((a, b) => a + b, 0)

  return counts
}
