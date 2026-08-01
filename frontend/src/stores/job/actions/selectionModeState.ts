import type { BatchJobTarget } from '../../../api/batchTarget'
import type { JobListFilterParams } from '../../../types/jobTypes'
import { matchesFilterPayload } from '../filterLogic/matchesFilterPayload'
import type { JobState } from '../state'

export const FAILED_SELECTION_FILTER: JobListFilterParams = {
  status: 'failed',
  search: null,
  workflow_version: null,
  workflow_version_none: false,
  active_node_key: null,
}

export const UNPACKED_SELECTION_FILTER: JobListFilterParams = {
  status: 'completed',
  search: null,
  workflow_version: null,
  workflow_version_none: false,
  active_node_key: null,
  packed: 0,
}

export function clearedSelectionState() {
  return {
    selectionMode: 'explicit' as const,
    selectionFilter: null,
    excludedIds: new Set<string>(),
    selectedIds: new Set<string>(),
    selectionCount: null,
  }
}

export function enterAllMatchingState(
  filter: JobListFilterParams,
  count: number | null
) {
  return {
    selectionMode: 'allMatching' as const,
    selectionFilter: filter,
    excludedIds: new Set<string>(),
    selectedIds: new Set<string>(),
    selectionCount: count,
  }
}

/** Count loaded jobs matching the selection filter (fallback when the
 * server-side count is unknown). */
export function countLoadedMatching(state: JobState): number {
  if (!state.selectionFilter) return 0
  let count = 0
  for (const id of state.jobIds) {
    const job = state.jobsById[id]
    if (job && matchesFilterPayload(job, state.selectionFilter)) count += 1
  }
  return count
}

/** Resolve the current selection to a batch endpoint target, or null when
 * nothing is selected. */
export function resolveBatchTarget(state: JobState): BatchJobTarget | null {
  if (state.selectionMode === 'allMatching' && state.selectionFilter) {
    return {
      filter: state.selectionFilter,
      excludeIds: Array.from(state.excludedIds),
    }
  }
  const ids = Array.from(state.selectedIds)
  return ids.length > 0 ? { jobIds: ids } : null
}

/** Explicit id overrides (dialog-scoped reruns) win over the selection. */
export function resolveOpTarget(
  state: JobState,
  jobIds?: string[]
): BatchJobTarget | null {
  if (jobIds) return jobIds.length > 0 ? { jobIds } : null
  return resolveBatchTarget(state)
}

/** Post-batch-op refresh: drop the stale selection and refetch the first
 * page with the active filter instead of the legacy unbounded fetch. */
export async function refreshAfterBatchOperation(
  get: () => JobState,
  workspaceId: string
): Promise<void> {
  get().clearSelection()
  await get().refreshFirstPage(workspaceId)
}
