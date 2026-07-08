import type { JobSummary } from '../../../jobTypes'
import type { JobFilterConfig } from '../state'
import { normalizeJobStatus } from '../state'
import type { FilterCounts } from './types'
import { passesFilters } from './passesFilters'

export function computeFilteredJobIds(
  jobIds: string[],
  jobsById: Record<string, JobSummary>,
  filterConfig: JobFilterConfig
): string[] {
  const ids: string[] = []
  for (const id of jobIds) {
    const job = jobsById[id]
    if (job && passesFilters(job, filterConfig)) ids.push(id)
  }
  return ids
}

export function computeFilterCounts(
  jobIds: string[],
  jobsById: Record<string, JobSummary>,
  filterConfig: JobFilterConfig
): FilterCounts {
  const counts = emptyCounts()
  for (const id of jobIds) {
    const job = jobsById[id]
    if (job) addJobToCounts(counts, job, filterConfig)
  }
  return counts
}

export function applyAppendToFilteredIds(
  currentIds: string[],
  appendedJobs: JobSummary[],
  filterConfig: JobFilterConfig
): string[] {
  if (appendedJobs.length === 0) return currentIds
  const next = [...currentIds]
  for (const job of appendedJobs) {
    if (passesFilters(job, filterConfig)) next.push(job.id)
  }
  return next.length === currentIds.length ? currentIds : next
}

export function applyAppendToFilterCounts(
  currentCounts: FilterCounts,
  appendedJobs: JobSummary[],
  filterConfig: JobFilterConfig
): FilterCounts {
  if (appendedJobs.length === 0) return currentCounts
  const counts = cloneCounts(currentCounts)
  for (const job of appendedJobs) addJobToCounts(counts, job, filterConfig)
  return counts
}

export function applyPatchToFilteredIds(
  currentIds: string[],
  jobIndexById: Record<string, number>,
  oldJobsById: Record<string, JobSummary>,
  patchJobs: JobSummary[],
  deletedIds: string[],
  filterConfig: JobFilterConfig
): string[] {
  if (patchJobs.length === 0 && deletedIds.length === 0) return currentIds
  const removed = new Set(deletedIds)
  const addedAtFront: string[] = []
  const gainedExisting: string[] = []
  for (const job of patchJobs) {
    const oldJob = oldJobsById[job.id]
    const oldMatches = oldJob ? passesFilters(oldJob, filterConfig) : false
    const newMatches = passesFilters(job, filterConfig)
    if (oldMatches && !newMatches) removed.add(job.id)
    else if (!oldMatches && newMatches) {
      if (oldJob) gainedExisting.push(job.id)
      else addedAtFront.push(job.id)
    }
  }
  let next = currentIds.filter((id) => !removed.has(id))
  for (const id of gainedExisting) {
    insertByJobOrder(next, id, jobIndexById)
  }
  if (addedAtFront.length > 0) {
    next = [...addedAtFront, ...next]
  }
  if (
    next.length === currentIds.length &&
    next.every((id, index) => id === currentIds[index])
  ) {
    return currentIds
  }
  return next
}

export function applyPatchToFilterCounts(
  currentCounts: FilterCounts,
  oldJobsById: Record<string, JobSummary>,
  patchJobs: JobSummary[],
  deletedIds: string[],
  filterConfig: JobFilterConfig
): FilterCounts {
  if (patchJobs.length === 0 && deletedIds.length === 0) return currentCounts
  const counts = cloneCounts(currentCounts)
  const deleted = new Set(deletedIds)
  for (const id of deletedIds) {
    const oldJob = oldJobsById[id]
    if (oldJob) removeJobFromCounts(counts, oldJob, filterConfig)
  }
  for (const job of patchJobs) {
    const oldJob = oldJobsById[job.id]
    if (oldJob && !deleted.has(job.id))
      removeJobFromCounts(counts, oldJob, filterConfig)
    addJobToCounts(counts, job, filterConfig)
  }
  return counts
}

export function applyDeleteToFilteredIds(
  currentIds: string[],
  deletedIds: string[]
): string[] {
  if (deletedIds.length === 0) return currentIds
  const deleted = new Set(deletedIds)
  return currentIds.filter((id) => !deleted.has(id))
}

export function applyDeleteToFilterCounts(
  currentCounts: FilterCounts,
  jobsById: Record<string, JobSummary>,
  deletedIds: string[],
  filterConfig: JobFilterConfig
): FilterCounts {
  if (deletedIds.length === 0) return currentCounts
  const counts = cloneCounts(currentCounts)
  for (const id of deletedIds) {
    const job = jobsById[id]
    if (job) removeJobFromCounts(counts, job, filterConfig)
  }
  return counts
}

function emptyCounts(): FilterCounts {
  return {
    status: {},
    workflowVersion: {},
    activeNodeKey: {},
  }
}

function cloneCounts(counts: FilterCounts): FilterCounts {
  return {
    status: { ...counts.status },
    workflowVersion: { ...counts.workflowVersion },
    activeNodeKey: { ...counts.activeNodeKey },
  }
}

function addJobToCounts(
  counts: FilterCounts,
  job: JobSummary,
  filterConfig: JobFilterConfig
): void {
  if (passesFilters(job, filterConfig, 'status')) {
    const key = normalizeJobStatus(job.status)
    counts.status[key] = (counts.status[key] ?? 0) + 1
    counts.status.all = (counts.status.all ?? 0) + 1
  }
  if (passesFilters(job, filterConfig, 'workflowVersion')) {
    const version = job.workflow_version
    if (version !== null && version !== undefined) {
      const key = String(version)
      counts.workflowVersion[key] = (counts.workflowVersion[key] ?? 0) + 1
    } else {
      counts.workflowVersion.none = (counts.workflowVersion.none ?? 0) + 1
    }
    counts.workflowVersion.all = (counts.workflowVersion.all ?? 0) + 1
  }
  if (passesFilters(job, filterConfig, 'activeNodeKey')) {
    const key = job.active_node_key
    if (key) {
      counts.activeNodeKey[key] = (counts.activeNodeKey[key] ?? 0) + 1
    }
    counts.activeNodeKey.all = (counts.activeNodeKey.all ?? 0) + 1
  }
}

function removeJobFromCounts(
  counts: FilterCounts,
  job: JobSummary,
  filterConfig: JobFilterConfig
): void {
  if (passesFilters(job, filterConfig, 'status')) {
    const key = normalizeJobStatus(job.status)
    counts.status[key] = (counts.status[key] ?? 0) - 1
    if (counts.status[key] <= 0) delete counts.status[key]
    counts.status.all = (counts.status.all ?? 0) - 1
    if (counts.status.all <= 0) delete counts.status.all
  }
  if (passesFilters(job, filterConfig, 'workflowVersion')) {
    const version = job.workflow_version
    if (version !== null && version !== undefined) {
      const key = String(version)
      counts.workflowVersion[key] = (counts.workflowVersion[key] ?? 0) - 1
      if (counts.workflowVersion[key] <= 0) delete counts.workflowVersion[key]
    } else {
      counts.workflowVersion.none = (counts.workflowVersion.none ?? 0) - 1
      if (counts.workflowVersion.none <= 0) delete counts.workflowVersion.none
    }
    counts.workflowVersion.all = (counts.workflowVersion.all ?? 0) - 1
    if (counts.workflowVersion.all <= 0) delete counts.workflowVersion.all
  }
  if (passesFilters(job, filterConfig, 'activeNodeKey')) {
    const key = job.active_node_key
    if (key) {
      counts.activeNodeKey[key] = (counts.activeNodeKey[key] ?? 0) - 1
      if (counts.activeNodeKey[key] <= 0) delete counts.activeNodeKey[key]
    }
    counts.activeNodeKey.all = (counts.activeNodeKey.all ?? 0) - 1
    if (counts.activeNodeKey.all <= 0) delete counts.activeNodeKey.all
  }
}

function insertByJobOrder(
  ids: string[],
  id: string,
  jobIndexById: Record<string, number>
): void {
  const position = jobIndexById[id]
  if (position === undefined) return
  let low = 0
  let high = ids.length
  while (low < high) {
    const mid = (low + high) >> 1
    const midPosition = jobIndexById[ids[mid]] ?? Number.MAX_SAFE_INTEGER
    if (midPosition < position) low = mid + 1
    else high = mid
  }
  ids.splice(low, 0, id)
}
