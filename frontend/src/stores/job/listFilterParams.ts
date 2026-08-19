import type { JobListFilterParams } from '../../types/jobTypes'
import type { JobFilterConfig } from './filterConfig'

/**
 * Map the UI filter config to the server list query params. Semantics must
 * stay aligned with passesFilters (status folding, 'none' version, search).
 */
export function toJobListFilterParams(
  config: JobFilterConfig
): JobListFilterParams {
  const search = config.search.trim()
  return {
    status: config.status,
    search: search === '' ? null : search,
    workflow_version:
      typeof config.workflowVersion === 'number'
        ? config.workflowVersion
        : null,
    workflow_version_none: config.workflowVersion === 'none',
    active_node_key: config.activeNodeKey,
    paused: config.paused,
  }
}
