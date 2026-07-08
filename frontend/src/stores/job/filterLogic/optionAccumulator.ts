import type { JobSummary } from '../../../jobTypes'
import type { WorkflowVersionOptions } from './types'

export interface JobFilterOptionAccumulator {
  nodeKeyCounts: Map<string, number>
  workflowVersionCounts: Map<number, number>
  missingWorkflowVersionCount: number
  nodeKeys: Set<string>
  workflowVersionOptions: WorkflowVersionOptions
}

export function createOptionAccumulator(
  jobs: JobSummary[]
): JobFilterOptionAccumulator {
  const nodeKeyCounts = new Map<string, number>()
  const workflowVersionCounts = new Map<number, number>()
  let missingWorkflowVersionCount = 0
  for (const job of jobs) {
    addJobContribution(job, nodeKeyCounts, workflowVersionCounts, () => {
      missingWorkflowVersionCount += 1
    })
  }
  return buildAccumulator(
    nodeKeyCounts,
    workflowVersionCounts,
    missingWorkflowVersionCount
  )
}

function buildAccumulator(
  nodeKeyCounts: Map<string, number>,
  workflowVersionCounts: Map<number, number>,
  missingWorkflowVersionCount: number
): JobFilterOptionAccumulator {
  return {
    nodeKeyCounts,
    workflowVersionCounts,
    missingWorkflowVersionCount,
    nodeKeys: new Set(nodeKeyCounts.keys()),
    workflowVersionOptions: {
      versionOptions: Array.from(workflowVersionCounts.keys()).sort(
        (a, b) => b - a
      ),
      hasMissingVersion: missingWorkflowVersionCount > 0,
    },
  }
}

export function addJobContribution(
  job: JobSummary,
  nodeKeyCounts: Map<string, number>,
  workflowVersionCounts: Map<number, number>,
  onMissingVersion: () => void
): void {
  if (job.active_node_key) {
    nodeKeyCounts.set(
      job.active_node_key,
      (nodeKeyCounts.get(job.active_node_key) ?? 0) + 1
    )
  }
  for (const node of job.node_summaries ?? []) {
    nodeKeyCounts.set(
      node.node_key,
      (nodeKeyCounts.get(node.node_key) ?? 0) + 1
    )
  }
  if (job.workflow_version !== null && job.workflow_version !== undefined) {
    workflowVersionCounts.set(
      job.workflow_version,
      (workflowVersionCounts.get(job.workflow_version) ?? 0) + 1
    )
  } else {
    onMissingVersion()
  }
}

export function removeJobContribution(
  job: JobSummary,
  nodeKeyCounts: Map<string, number>,
  workflowVersionCounts: Map<number, number>,
  onMissingVersion: () => void
): void {
  decrement(nodeKeyCounts, job.active_node_key)
  for (const node of job.node_summaries ?? []) {
    decrement(nodeKeyCounts, node.node_key)
  }
  if (job.workflow_version !== null && job.workflow_version !== undefined) {
    decrement(workflowVersionCounts, job.workflow_version)
  } else {
    onMissingVersion()
  }
}

function decrement<K>(map: Map<K, number>, key: K | null | undefined): void {
  if (key === null || key === undefined) return
  const count = (map.get(key) ?? 0) - 1
  if (count <= 0) {
    map.delete(key)
  } else {
    map.set(key, count)
  }
}

export function applyAppendToAccumulator(
  acc: JobFilterOptionAccumulator,
  jobs: JobSummary[]
): void {
  for (const job of jobs) {
    addJobContribution(
      job,
      acc.nodeKeyCounts,
      acc.workflowVersionCounts,
      () => {
        acc.missingWorkflowVersionCount += 1
      }
    )
  }
  syncAccumulator(acc)
}

export function applyPatchToAccumulator(
  acc: JobFilterOptionAccumulator,
  jobsById: Record<string, JobSummary>,
  patchJobs: JobSummary[],
  deletedJobIds: string[]
): void {
  const deleted = new Set(deletedJobIds)
  for (const id of deleted) {
    const oldJob = jobsById[id]
    if (oldJob) {
      removeJobContribution(
        oldJob,
        acc.nodeKeyCounts,
        acc.workflowVersionCounts,
        () => {
          acc.missingWorkflowVersionCount -= 1
        }
      )
    }
  }
  for (const job of patchJobs) {
    const oldJob = jobsById[job.id]
    if (oldJob && !deleted.has(job.id)) {
      removeJobContribution(
        oldJob,
        acc.nodeKeyCounts,
        acc.workflowVersionCounts,
        () => {
          acc.missingWorkflowVersionCount -= 1
        }
      )
    }
    addJobContribution(
      job,
      acc.nodeKeyCounts,
      acc.workflowVersionCounts,
      () => {
        acc.missingWorkflowVersionCount += 1
      }
    )
  }
  syncAccumulator(acc)
}

export function syncAccumulator(acc: JobFilterOptionAccumulator): void {
  syncNodeKeys(acc)
  syncWorkflowVersionOptions(acc)
}

function syncNodeKeys(acc: JobFilterOptionAccumulator): void {
  const current = acc.nodeKeys
  if (current.size === acc.nodeKeyCounts.size) {
    let same = true
    for (const key of acc.nodeKeyCounts.keys()) {
      if (!current.has(key)) {
        same = false
        break
      }
    }
    if (same) return
  }
  acc.nodeKeys = new Set(acc.nodeKeyCounts.keys())
}

function syncWorkflowVersionOptions(acc: JobFilterOptionAccumulator): void {
  const nextVersions = Array.from(acc.workflowVersionCounts.keys()).sort(
    (a, b) => b - a
  )
  const current = acc.workflowVersionOptions
  const nextMissing = acc.missingWorkflowVersionCount > 0
  if (
    nextVersions.length === current.versionOptions.length &&
    nextVersions.every(
      (version, index) => version === current.versionOptions[index]
    ) &&
    nextMissing === current.hasMissingVersion
  ) {
    return
  }
  acc.workflowVersionOptions = {
    versionOptions: nextVersions,
    hasMissingVersion: nextMissing,
  }
}
