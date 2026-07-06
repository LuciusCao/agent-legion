import { useMemo } from 'react'
import type { JobSummary } from '../jobTypes'

export interface WorkflowVersionOptions {
  versionOptions: number[]
  hasMissingVersion: boolean
}

export function useWorkflowVersionOptions(
  jobs: JobSummary[]
): WorkflowVersionOptions {
  return useMemo(() => {
    const versions = new Set<number>()
    let hasMissingVersion = false
    for (const job of jobs) {
      if (job.workflow_version !== null && job.workflow_version !== undefined) {
        versions.add(job.workflow_version)
      } else {
        hasMissingVersion = true
      }
    }
    return {
      versionOptions: Array.from(versions).sort((a, b) => b - a),
      hasMissingVersion,
    }
  }, [jobs])
}
