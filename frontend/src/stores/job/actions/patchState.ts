import type { JobSummary } from '../../../types/jobTypes'
import type { JobState } from '../state'
import { applyPatchToAccumulator } from '../filterLogic/optionAccumulator'
import { applyVisiblePatchJobs } from '../filterLogic/patchVisibility'
import {
  applyPatchToFilteredIds,
  applyPatchToFilterCounts,
} from '../filterLogic/incrementalFilters'
import { buildPatchedCollections } from './patchCollections'

export function applyJobPatchBatchUpdate(
  state: JobState,
  workspaceId: string,
  revision: number,
  patchJobs: JobSummary[],
  deletedJobIds: string[]
): Partial<JobState> | null {
  if (state.jobsWorkspaceId !== workspaceId || revision <= state.revision)
    return null
  const deleted = new Set(deletedJobIds)
  const oldJobsById = state.jobsById
  const jobsById = { ...oldJobsById }
  for (const id of deleted) delete jobsById[id]
  const filterConfig = state.filterConfig
  const visibleJobs = applyVisiblePatchJobs(jobsById, patchJobs, filterConfig)
  const { jobs, jobIds, jobIndexById } = buildPatchedCollections(
    state,
    jobsById,
    visibleJobs,
    deleted
  )
  applyPatchToAccumulator(
    state.optionAccumulator,
    oldJobsById,
    visibleJobs,
    deletedJobIds
  )
  return {
    jobs,
    jobsById,
    jobIds,
    jobIndexById,
    filteredJobIds: applyPatchToFilteredIds(
      state.filteredJobIds,
      jobIndexById,
      oldJobsById,
      visibleJobs,
      deletedJobIds,
      filterConfig
    ),
    filterCounts: applyPatchToFilterCounts(
      state.filterCounts,
      oldJobsById,
      visibleJobs,
      deletedJobIds,
      filterConfig
    ),
    revision,
    isLoading: false,
    error: null,
  }
}
