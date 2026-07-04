import type { JobState } from '../state'
import { normalizeJobStatus } from '../state'
import type { FilterCounts } from './types'
import { passesFilters } from './passesFilters'

export function getFilterCounts(state: JobState): FilterCounts {
  const counts: FilterCounts = {
    status: {},
    workflowVersion: {},
    activeNodeKey: {},
  }

  for (const job of state.jobs) {
    if (passesFilters(job, state.filterConfig, 'status')) {
      const key = normalizeJobStatus(job.status)
      counts.status[key] = (counts.status[key] ?? 0) + 1
    }
    if (passesFilters(job, state.filterConfig, 'workflowVersion')) {
      const version = job.workflow_version
      if (version !== null && version !== undefined) {
        counts.workflowVersion[String(version)] =
          (counts.workflowVersion[String(version)] ?? 0) + 1
      }
    }
    if (passesFilters(job, state.filterConfig, 'activeNodeKey')) {
      const key = job.active_node_key
      if (key) {
        counts.activeNodeKey[key] = (counts.activeNodeKey[key] ?? 0) + 1
      }
    }
  }

  return counts
}
