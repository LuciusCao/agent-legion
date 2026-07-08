import type { JobSummary } from '../../../jobTypes'
import type { JobState } from '../state'
import {
  applyAppendToFilteredIds,
  applyAppendToFilterCounts,
} from '../filterLogic/incrementalFilters'
import { applyAppendToAccumulator } from '../filterLogic/optionAccumulator'

export function appendJobsSnapshotUpdate(
  state: JobState,
  workspaceId: string,
  jobs: JobSummary[]
): Partial<JobState> {
  if (state.jobsWorkspaceId !== workspaceId) return {}
  const known = new Set(state.jobIds)
  const added = jobs.filter((job) => !known.has(job.id))
  if (added.length === 0) return {}
  const jobsById = { ...state.jobsById }
  for (const job of added) jobsById[job.id] = job
  const jobIds = [...state.jobIds, ...added.map((job) => job.id)]
  const nextJobs = [...state.jobs, ...added]
  const jobIndexById = { ...state.jobIndexById }
  for (let i = state.jobs.length; i < nextJobs.length; i++) {
    jobIndexById[nextJobs[i].id] = i
  }
  applyAppendToAccumulator(state.optionAccumulator, added)
  return {
    jobs: nextJobs,
    jobsById,
    jobIds,
    jobIndexById,
    optionAccumulator: state.optionAccumulator,
    filteredJobIds: applyAppendToFilteredIds(
      state.filteredJobIds,
      added,
      state.filterConfig
    ),
    filterCounts: applyAppendToFilterCounts(
      state.filterCounts,
      added,
      state.filterConfig
    ),
  }
}
