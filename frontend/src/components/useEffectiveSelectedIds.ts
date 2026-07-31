import { useMemo } from 'react'
import { useJobStore } from '../stores/jobStore'
import { matchesFilterPayload } from '../stores/job/filterLogic/matchesFilterPayload'

/**
 * Effective checked ids for the job list rows. In 'allMatching' selection
 * mode every loaded row matching the selection filter renders checked
 * unless it was explicitly excluded; in explicit mode it is selectedIds.
 */
export function useEffectiveSelectedIds(jobIds: string[]): Set<string> {
  const selectedIds = useJobStore((state) => state.selectedIds)
  const selectionMode = useJobStore((state) => state.selectionMode)
  const selectionFilter = useJobStore((state) => state.selectionFilter)
  const excludedIds = useJobStore((state) => state.excludedIds)
  const jobsById = useJobStore((state) => state.jobsById)
  return useMemo(() => {
    if (selectionMode !== 'allMatching' || !selectionFilter) {
      return selectedIds
    }
    const next = new Set<string>()
    for (const id of jobIds) {
      const job = jobsById[id]
      if (
        job &&
        !excludedIds.has(id) &&
        matchesFilterPayload(job, selectionFilter)
      ) {
        next.add(id)
      }
    }
    return next
  }, [
    jobIds,
    jobsById,
    selectedIds,
    selectionMode,
    selectionFilter,
    excludedIds,
  ])
}
