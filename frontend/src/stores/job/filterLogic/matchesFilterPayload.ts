import type { JobListFilterParams, JobSummary } from '../../../types/jobTypes'
import { normalizeJobStatus } from '../state'
import { matchesSearch } from './matchesSearch'

/**
 * Client-side mirror of the server list filter semantics for a
 * JobFilterPayload; stays aligned with passesFilters (status folding,
 * 'none' version, search) plus the packed dimension.
 */
export function matchesFilterPayload(
  job: JobSummary,
  payload: JobListFilterParams
): boolean {
  if (payload.status && normalizeJobStatus(job.status) !== payload.status) {
    return false
  }
  if (
    payload.workflow_version != null &&
    job.workflow_version !== payload.workflow_version
  ) {
    return false
  }
  if (payload.workflow_version_none && job.workflow_version != null) {
    return false
  }
  if (
    payload.active_node_key &&
    job.active_node_key !== payload.active_node_key
  ) {
    return false
  }
  if (payload.search && !matchesSearch(job, payload.search)) return false
  if (payload.packed != null && (job.packed ?? 0) !== payload.packed) {
    return false
  }
  if (
    payload.paused != null &&
    (job.execution_control?.paused ?? false) !== payload.paused
  ) {
    return false
  }
  return true
}
