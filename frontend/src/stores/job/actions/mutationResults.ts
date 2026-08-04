import { useUiStore } from '../../uiStore'
import { countMutationResults, makeMutationToast } from '../mutationHelpers'
import type { JobStoreSet } from '../state'

export function clearSucceededSelection(
  set: JobStoreSet,
  results: { job_id: string; status: string }[]
): void {
  const succeededIds = new Set(
    results.filter((r) => r.status === 'succeeded').map((r) => r.job_id)
  )
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

export function applyMutationResults(
  set: JobStoreSet,
  results: { job_id: string; status: 'succeeded' | 'skipped' | 'failed' }[],
  label: string,
  toastSuffix = ''
): void {
  clearSucceededSelection(set, results)
  const counts = countMutationResults(results)
  useUiStore
    .getState()
    .showToast(
      makeMutationToast(label, counts) + toastSuffix,
      counts.failed > 0 ? 'error' : 'success'
    )
}
