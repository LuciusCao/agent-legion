import type { JobSummary, WorkflowDefinitionRecord } from '../types'
import type { CatalogSource } from './nodeCatalog'
import type { DagNode } from './jobDag'

export type WorkflowNodesByKey = Record<string, WorkflowDefinitionRecord>

export function nodesForJob(
  job: JobSummary,
  workflowNodesByKey?: WorkflowNodesByKey | null,
  workflowDefinition?: CatalogSource
): DagNode[] | null {
  // `type: start` entry nodes never execute and never appear in job_nodes;
  // hide them from job views (rerun / run-to pickers, DAG ordering).
  // Matching key is workspace_id: job.workflow_key is deprecated (#211
  // Phase 2) and always equals the workspace id since schema v62, and the
  // catalog/definition keys are the workspace-bound workflow key.
  const executable = (nodes: DagNode[]) =>
    nodes.filter((node) => node.node_type !== 'start')
  if (workflowNodesByKey && job.workspace_id in workflowNodesByKey) {
    return executable(workflowNodesByKey[job.workspace_id].nodes)
  }
  if (workflowDefinition && job.workspace_id === workflowDefinition.key) {
    return executable(workflowDefinition.nodes)
  }
  return null
}

export function computeOrderedNodes(
  jobs: JobSummary[],
  workflowDefinition: CatalogSource,
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
  // Distinct-workflow detection via workspace_id (deprecated workflow_key's
  // identical twin, #211 Phase 2).
  const knownWorkflowKeys = new Set(
    jobsWithNodes.map((entry) => entry.job.workspace_id)
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
  workflowDefinition: CatalogSource
): JobSummary[] {
  return jobs.filter((job) => {
    const nodes = nodesForJob(job, workflowNodesByKey, workflowDefinition)
    if (!nodes) return true
    return !nodes.some((n) => n.key === nodeKey)
  })
}
