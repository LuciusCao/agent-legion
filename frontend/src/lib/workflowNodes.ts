import type { JobSummary, WorkflowDefinitionRecord } from '../types'
import type { DagNode } from './jobDag'

export type WorkflowNodesByKey = Record<string, WorkflowDefinitionRecord>

export function nodesForJob(
  job: JobSummary,
  workflowNodesByKey?: WorkflowNodesByKey | null,
  workflowDefinition?: WorkflowDefinitionRecord | null
): DagNode[] | null {
  if (workflowNodesByKey && job.workflow_key in workflowNodesByKey) {
    return workflowNodesByKey[job.workflow_key].nodes
  }
  if (workflowDefinition && job.workflow_key === workflowDefinition.key) {
    return workflowDefinition.nodes
  }
  return null
}

export function computeOrderedNodes(
  jobs: JobSummary[],
  workflowDefinition: WorkflowDefinitionRecord | null | undefined,
  workflowNodesByKey: WorkflowNodesByKey | null | undefined
): DagNode[] {
  if (jobs.length === 0) return []

  const jobsWithNodes = jobs
    .map((job) => ({
      job,
      nodes: nodesForJob(job, workflowNodesByKey, workflowDefinition),
    }))
    .filter(
      (
        entry
      ): entry is { job: JobSummary; nodes: NonNullable<typeof entry.nodes> } =>
        !!entry.nodes
    )

  if (jobsWithNodes.length === 0) return []

  const firstNodes = jobsWithNodes[0].nodes
  const knownWorkflowKeys = new Set(
    jobsWithNodes.map((entry) => entry.job.workflow_key)
  )
  const hasMultipleKnownWorkflows = knownWorkflowKeys.size > 1

  if (!hasMultipleKnownWorkflows) {
    return firstNodes
  }

  const keySets = jobsWithNodes.map(
    (entry) => new Set(entry.nodes.map((n) => n.key))
  )
  const commonKeys = new Set(
    [...keySets[0]].filter((key) => keySets.every((set) => set.has(key)))
  )

  return firstNodes.filter((n) => commonKeys.has(n.key))
}

export function excludedJobs(
  jobs: JobSummary[],
  nodeKey: string,
  workflowNodesByKey: WorkflowNodesByKey | null | undefined,
  workflowDefinition: WorkflowDefinitionRecord | null | undefined
): JobSummary[] {
  return jobs.filter((job) => {
    const nodes = nodesForJob(job, workflowNodesByKey, workflowDefinition)
    if (!nodes) return true
    return !nodes.some((n) => n.key === nodeKey)
  })
}
