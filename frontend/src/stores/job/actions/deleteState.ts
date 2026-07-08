import type { JobState } from '../state'
import { applyPatchToAccumulator } from '../filterLogic/optionAccumulator'
import {
  applyDeleteToFilteredIds,
  applyDeleteToFilterCounts,
} from '../filterLogic/incrementalFilters'

export function deleteSucceededJobs(
  state: JobState,
  succeededIds: Set<string>
): Partial<JobState> {
  const nextSelected = new Set(state.selectedIds)
  const jobsById = { ...state.jobsById }
  const jobIds = state.jobIds.filter((id) => {
    if (!succeededIds.has(id)) return true
    nextSelected.delete(id)
    delete jobsById[id]
    return false
  })
  const jobs = jobIds.map((id) => jobsById[id]).filter(Boolean)
  applyPatchToAccumulator(
    state.optionAccumulator,
    state.jobsById,
    [],
    [...succeededIds]
  )
  return {
    jobs,
    jobsById,
    jobIds,
    jobIndexById: Object.fromEntries(jobs.map((job, index) => [job.id, index])),
    filteredJobIds: applyDeleteToFilteredIds(state.filteredJobIds, [
      ...succeededIds,
    ]),
    filterCounts: applyDeleteToFilterCounts(
      state.filterCounts,
      state.jobsById,
      [...succeededIds],
      state.filterConfig
    ),
    selectedIds: nextSelected,
    selectMode: nextSelected.size === 0 ? false : state.selectMode,
  }
}
