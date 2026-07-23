import type { JobSummary } from '../../../types/jobTypes'
import type { JobState } from '../state'

export function buildPatchedCollections(
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
