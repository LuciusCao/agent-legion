import { useMemo, useState } from 'react'
import { normalizeJobStatus } from '../../stores/job/state'
import type { JobSummary, WorkflowDefinitionRecord } from '../../types'
import {
  computeOrderedNodes,
  excludedJobs,
  type WorkflowNodesByKey,
} from '../../lib/workflowNodes'

export type UseJobRerunDialogOptions = {
  jobs: JobSummary[]
  workflowDefinition?: WorkflowDefinitionRecord | null
  workflowNodesByKey?: WorkflowNodesByKey | null
}

export function useJobRerunDialog({
  jobs,
  workflowDefinition,
  workflowNodesByKey,
}: UseJobRerunDialogOptions) {
  const orderedNodes = useMemo(
    () => computeOrderedNodes(jobs, workflowDefinition, workflowNodesByKey),
    [jobs, workflowDefinition, workflowNodesByKey]
  )

  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(
    orderedNodes[0]?.key ?? null
  )
  const [failedMode, setFailedMode] = useState(false)

  const effectiveNodeKey = useMemo(() => {
    if (failedMode) return null
    if (selectedNodeKey && orderedNodes.some((n) => n.key === selectedNodeKey)) {
      return selectedNodeKey
    }
    return orderedNodes[0]?.key ?? null
  }, [failedMode, orderedNodes, selectedNodeKey])

  const failedJobs = useMemo(
    () => jobs.filter((j) => normalizeJobStatus(j.status) === 'failed'),
    [jobs]
  )
  const nonFailedJobs = useMemo(
    () => jobs.filter((j) => normalizeJobStatus(j.status) !== 'failed'),
    [jobs]
  )

  const excluded = useMemo(
    () =>
      effectiveNodeKey
        ? excludedJobs(
            jobs,
            effectiveNodeKey,
            workflowNodesByKey,
            workflowDefinition
          )
        : [],
    [jobs, effectiveNodeKey, workflowNodesByKey, workflowDefinition]
  )

  return {
    orderedNodes,
    selectedNodeKey,
    setSelectedNodeKey,
    failedMode,
    setFailedMode,
    effectiveNodeKey,
    failedJobs,
    nonFailedJobs,
    excluded,
  }
}
