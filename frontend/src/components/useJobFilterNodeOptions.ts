import { useMemo } from 'react'
import type { JobSummary } from '../jobTypes'
import type { WorkflowDefinitionRecord } from '../types'

export interface JobFilterNodeOption {
  key: string
  label: string
}

export function useJobFilterNodeOptions(
  workflowDefinition: WorkflowDefinitionRecord | null,
  jobs: JobSummary[]
): JobFilterNodeOption[] {
  return useMemo(() => {
    const defined = new Map<string, string>()
    for (const node of workflowDefinition?.nodes ?? []) {
      defined.set(node.key, node.label)
    }
    const seen = new Set<string>()
    for (const job of jobs) {
      if (job.active_node_key) seen.add(job.active_node_key)
      for (const node of job.node_summaries ?? []) {
        seen.add(node.node_key)
      }
    }
    const options: JobFilterNodeOption[] = []
    for (const [key, label] of defined) {
      options.push({ key, label })
      seen.delete(key)
    }
    for (const key of seen) {
      options.push({ key, label: key })
    }
    return options
  }, [workflowDefinition, jobs])
}
