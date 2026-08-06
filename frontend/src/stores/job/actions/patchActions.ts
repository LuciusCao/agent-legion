import type { JobSummary } from '../../../types/jobTypes'
import type { JobState, JobStoreSet } from '../state'
import { applyPatchToAccumulator } from '../filterLogic/optionAccumulator'
import { applyVisiblePatchJobs } from '../filterLogic/patchVisibility'
import {
  applyPatchToFilteredIds,
  applyPatchToFilterCounts,
} from '../filterLogic/incrementalFilters'

function buildPatchedCollections(
  state: JobState,
  jobsById: Record<string, JobSummary>,
  patchJobs: JobSummary[],
  deleted: Set<string>
) {
  const known = new Set(state.jobIds)
  for (const id of deleted) known.delete(id)
  const added = patchJobs
    .filter((job) => !known.has(job.id))
    .map((job) => job.id)
  const reordered = deleted.size > 0 || added.length > 0
  const jobIds = reordered
    ? [...added, ...state.jobIds.filter((id) => !deleted.has(id))]
    : state.jobIds
  const jobIndexById = reordered
    ? Object.fromEntries(jobIds.map((id, index) => [id, index]))
    : state.jobIndexById
  return {
    jobIds,
    jobIndexById,
    jobs: patchJobsArray(state, jobsById, patchJobs, reordered, jobIds),
  }
}

function patchJobsArray(
  state: JobState,
  jobsById: Record<string, JobSummary>,
  patchJobs: JobSummary[],
  reordered: boolean,
  jobIds: string[]
) {
  const jobs = reordered
    ? jobIds.map((id) => jobsById[id]).filter(Boolean)
    : state.jobs.slice()
  if (!reordered) {
    for (const job of patchJobs) {
      const index = state.jobIndexById[job.id]
      if (index !== undefined) jobs[index] = job
    }
  }
  return jobs
}

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

export function patchActions(set: JobStoreSet) {
  return {
    applyJobPatchBatch: (
      workspaceId: string,
      revision: number,
      patchJobs: JobSummary[],
      deletedJobIds: string[]
    ) =>
      set(
        (state) =>
          applyJobPatchBatchUpdate(
            state,
            workspaceId,
            revision,
            patchJobs,
            deletedJobIds
          ) ?? {}
      ),
  }
}
