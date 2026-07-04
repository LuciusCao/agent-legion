import { useUiStore } from '../../uiStore'
import {
  countMutationResults,
  makeMutationToast,
  type JobStoreSet,
} from '../state'

export function succeededIdsFromResults(
  results: { status: string; job_id: string }[]
): Set<string> {
  return new Set(
    results.filter((r) => r.status === 'succeeded').map((r) => r.job_id)
  )
}

export function deselectSucceeded(
  set: JobStoreSet,
  succeededIds: Set<string>
): void {
  set((state) => {
    const nextSelected = new Set(state.selectedIds)
    for (const id of succeededIds) {
      nextSelected.delete(id)
    }
    return {
      selectedIds: nextSelected,
      selectMode: nextSelected.size === 0 ? false : state.selectMode,
    }
  })
}

export function showMutationToast(
  action: string,
  results: { status: 'succeeded' | 'skipped' | 'failed' }[]
): void {
  const counts = countMutationResults(results)
  useUiStore
    .getState()
    .showToast(
      makeMutationToast(action, counts),
      counts.failed > 0 ? 'error' : 'success'
    )
}
