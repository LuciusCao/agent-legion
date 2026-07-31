import type { JobListFilterParams } from '../types/jobTypes'

/**
 * Batch endpoint target: either an explicit id list, or a server-side
 * filter plus explicit exclusions (mutually exclusive, validated by the
 * backend). Field types derive from the generated request schemas.
 */
export type BatchJobTarget =
  | { jobIds: string[] }
  | { filter: JobListFilterParams; excludeIds: string[] }

export function targetBody(target: BatchJobTarget): {
  job_ids?: string[]
  filter?: JobListFilterParams
  exclude_ids?: string[]
} {
  if ('jobIds' in target) return { job_ids: target.jobIds }
  return { filter: target.filter, exclude_ids: target.excludeIds }
}
