import { useMemo } from 'react'
import { useJobStore } from '../stores/jobStore'
import { matchesFilterPayload } from '../stores/job/filterLogic/matchesFilterPayload'
import type { JobSummary } from '../types'

export type WorkspaceSelection = {
  /** Full job objects for the action bar: explicit selection, or the
   * loaded non-excluded matching jobs in 'allMatching' mode. */
  selectedJobs: JobSummary[]
  /** Display count: explicit size, or matched total minus exclusions. */
  selectedCount: number
  /** Non-null when the selection is filter-based ('allMatching' mode). */
  allMatchingCount: number | null
}

export function useWorkspaceSelection(): WorkspaceSelection {
  const selectedIds = useJobStore((state) => state.selectedIds)
  const jobsById = useJobStore((state) => state.jobsById)
  const jobIds = useJobStore((state) => state.jobIds)
  const selectionMode = useJobStore((state) => state.selectionMode)
  const selectionFilter = useJobStore((state) => state.selectionFilter)
  const excludedIds = useJobStore((state) => state.excludedIds)
  const selectionCount = useJobStore((state) => state.selectionCount)
  const allMatching = selectionMode === 'allMatching' && selectionFilter != null

  return useMemo(() => {
    if (!allMatching || !selectionFilter) {
      return {
        selectedJobs: Array.from(selectedIds)
          .map((id) => jobsById[id])
          .filter((job): job is JobSummary => job !== undefined),
        selectedCount: selectedIds.size,
        allMatchingCount: null,
      }
    }
    const matchingLoaded: JobSummary[] = []
    let matchingLoadedTotal = 0
    for (const id of jobIds) {
      const job = jobsById[id]
      if (!job || !matchesFilterPayload(job, selectionFilter)) continue
      matchingLoadedTotal += 1
      if (!excludedIds.has(id)) matchingLoaded.push(job)
    }
    const base = selectionCount ?? matchingLoadedTotal
    const count = Math.max(0, base - excludedIds.size)
    return {
      selectedJobs: matchingLoaded,
      selectedCount: count,
      allMatchingCount: count,
    }
  }, [
    allMatching,
    selectionFilter,
    selectionCount,
    excludedIds,
    selectedIds,
    jobsById,
    jobIds,
  ])
}
