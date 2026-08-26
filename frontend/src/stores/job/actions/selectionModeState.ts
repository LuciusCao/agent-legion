import type { BatchJobTarget } from '../../../api/batchTarget'
import type { JobListFilterParams } from '../../../types/jobTypes'
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

/** The only selection fields resolveBatchTarget reads; callers may pass the
 * whole JobState (store actions) or just the subscribed slice (hooks). */
export type BatchSelectionSlice = Pick<
  JobState,
  'selectionMode' | 'selectionFilter' | 'excludedIds' | 'selectedIds'
>

/** Resolve the current selection to a batch endpoint target, or null when
 * nothing is selected. */
export function resolveBatchTarget(
  selection: BatchSelectionSlice
): BatchJobTarget | null {
  if (selection.selectionMode === 'allMatching' && selection.selectionFilter) {
    return {
      filter: selection.selectionFilter,
      excludeIds: Array.from(selection.excludedIds),
    }
  }
  const ids = Array.from(selection.selectedIds)
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
