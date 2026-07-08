import type { JobSummary } from '../../../jobTypes'
import type { JobState } from '../state'
import { applyPatchToAccumulator } from '../filterLogic/optionAccumulator'
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
  for (const job of patchJobs) jobsById[job.id] = job
  const { jobs, jobIds, jobIndexById } = buildPatchedCollections(
    state,
    jobsById,
    patchJobs,
    deleted
  )
  const filterConfig = state.filterConfig
  applyPatchToAccumulator(
    state.optionAccumulator,
    oldJobsById,
    patchJobs,
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
      patchJobs,
      deletedJobIds,
      filterConfig
    ),
    filterCounts: applyPatchToFilterCounts(
      state.filterCounts,
      oldJobsById,
      patchJobs,
      deletedJobIds,
      filterConfig
    ),
    revision,
    isLoading: false,
    error: null,
  }
}
