import type { JobFilterConfig } from '../state'
import type { JobSummary } from '../../../jobTypes'
import { normalizeJobStatus } from '../state'
import type { FilterDimension } from './types'
import { matchesSearch } from './matchesSearch'

export function passesFilters(
  job: JobSummary,
  config: JobFilterConfig,
  exclude?: FilterDimension
): boolean {
  if (exclude !== 'status' && config.status !== 'all') {
    if (normalizeJobStatus(job.status) !== config.status) return false
  }
  if (
    exclude !== 'workflowVersion' &&
    config.workflowVersion !== null &&
    job.workflow_version !== config.workflowVersion
  ) {
    return false
  }
  if (
    exclude !== 'activeNodeKey' &&
    config.activeNodeKey !== null &&
    job.active_node_key !== config.activeNodeKey
  ) {
    return false
  }
  if (exclude !== 'search' && !matchesSearch(job, config.search)) {
    return false
  }
  return true
}
