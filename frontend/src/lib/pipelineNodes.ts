import type { JobSummary, PipelineDefinitionRecord } from '../types'
import type { DagNode } from './jobDag'

export type PipelineNodesByKey = Record<string, PipelineDefinitionRecord>

export function nodesForJob(
  job: JobSummary,
  pipelineNodesByKey?: PipelineNodesByKey | null,
  pipelineDefinition?: PipelineDefinitionRecord | null
): DagNode[] | null {
  if (pipelineNodesByKey && job.pipeline_key in pipelineNodesByKey) {
    return pipelineNodesByKey[job.pipeline_key].nodes
  }
  if (pipelineDefinition && job.pipeline_key === pipelineDefinition.key) {
    return pipelineDefinition.nodes
  }
  return null
}

export function computeOrderedNodes(
  jobs: JobSummary[],
  pipelineDefinition: PipelineDefinitionRecord | null | undefined,
  pipelineNodesByKey: PipelineNodesByKey | null | undefined
): DagNode[] {
  if (jobs.length === 0) return []

  const jobsWithNodes = jobs
    .map((job) => ({
      job,
      nodes: nodesForJob(job, pipelineNodesByKey, pipelineDefinition),
    }))
    .filter(
      (
        entry
      ): entry is { job: JobSummary; nodes: NonNullable<typeof entry.nodes> } =>
        !!entry.nodes
    )

  if (jobsWithNodes.length === 0) return []

  const firstNodes = jobsWithNodes[0].nodes
  const knownPipelineKeys = new Set(
    jobsWithNodes.map((entry) => entry.job.pipeline_key)
  )
  const hasMultipleKnownPipelines = knownPipelineKeys.size > 1

  if (!hasMultipleKnownPipelines) {
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
  pipelineNodesByKey: PipelineNodesByKey | null | undefined,
  pipelineDefinition: PipelineDefinitionRecord | null | undefined
): JobSummary[] {
  return jobs.filter((job) => {
    const nodes = nodesForJob(job, pipelineNodesByKey, pipelineDefinition)
    if (!nodes) return true
    return !nodes.some((n) => n.key === nodeKey)
  })
}
