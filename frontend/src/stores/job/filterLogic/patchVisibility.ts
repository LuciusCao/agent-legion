import type { JobSummary } from '../../../types/jobTypes'
import type { JobFilterConfig } from '../state'
import { passesFilters } from './passesFilters'

/**
 * With server-side pagination the store only holds the loaded, filtered
 * pages. A patch for a job that is not loaded may belong to an unloaded page
 * or to a job outside the active filter, so only brand-new jobs that match
 * the current filter enter the list (prepended, newest first). Patches to
 * already-loaded jobs always apply so membership changes (e.g. the status
 * moves away from the filter) still remove them from the visible list.
 */
export function visibleIncomingPatchJobs(
  oldJobsById: Record<string, JobSummary>,
  patchJobs: JobSummary[],
  filterConfig: JobFilterConfig
): JobSummary[] {
  return patchJobs.filter(
    (job) =>
      oldJobsById[job.id] !== undefined || passesFilters(job, filterConfig)
  )
}

/**
 * Filter an incoming patch batch down to the jobs allowed into the loaded
 * list and upsert them into `jobsById` (already purged of deletions).
 * Returns the visible subset for the downstream collection updates.
 */
export function applyVisiblePatchJobs(
  jobsById: Record<string, JobSummary>,
  patchJobs: JobSummary[],
  filterConfig: JobFilterConfig
): JobSummary[] {
  const visible = visibleIncomingPatchJobs(jobsById, patchJobs, filterConfig)
  for (const job of visible) jobsById[job.id] = job
  return visible
}
