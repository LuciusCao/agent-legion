import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchFailedNodeRuns } from '../../api'
import type { JobSummary } from '../../types'
import type {
  FailedNodeRunItem,
  FailureCategory,
} from '../../types/failureTypes'
import {
  countJobsByFailureCategory,
  failedModeConfirmLabel,
  type FailureCategoryCounts,
  type FailureCategorySelection,
} from './failureCategoryCounts'

export type FailureCategoryContext = {
  workspaceId: string
  workflowKey?: string | null
}

export type JobRerunConfirmArgs = [
  nodeKey: string | null,
  fromFailedNode: boolean,
  jobIds?: string[],
  failureCategory?: FailureCategory,
]

export type FailureCategoryState = {
  selection: FailureCategorySelection
  setSelection: (value: FailureCategorySelection) => void
  counts: FailureCategoryCounts | null
  failedCount: number
  canConfirm: boolean
  confirmLabel: string
  confirmArgs: () => JobRerunConfirmArgs
}

/**
 * 失败类别子选项状态：failedMode 激活时懒加载一次类别计数，
 * 加载失败静默降级为不显示计数（counts 保持 null）。
 */
export function useFailureCategories(
  failedMode: boolean,
  failureContext: FailureCategoryContext | undefined,
  failedJobs: JobSummary[]
): FailureCategoryState {
  const [selection, setSelection] = useState<FailureCategorySelection>('all')
  const [failedRuns, setFailedRuns] = useState<FailedNodeRunItem[] | null>(null)
  const requestedRef = useRef(false)

  const workspaceId = failureContext?.workspaceId
  const workflowKey = failureContext?.workflowKey

  useEffect(() => {
    if (!failedMode || !workspaceId || requestedRef.current) return
    requestedRef.current = true
    let stale = false
    fetchFailedNodeRuns(workspaceId, { workflowKey })
      .then((data) => {
        if (stale) return
        setFailedRuns(data.runs ?? [])
      })
      .catch(() => {
        // Silent degradation: chips stay visible without counts.
      })
    return () => {
      stale = true
    }
  }, [failedMode, workspaceId, workflowKey])

  const failedJobIds = useMemo(
    () => failedJobs.map((job) => job.id),
    [failedJobs]
  )
  const counts = useMemo(
    () =>
      failedRuns ? countJobsByFailureCategory(failedRuns, failedJobIds) : null,
    [failedRuns, failedJobIds]
  )

  const failedCount = failedJobs.length
  const canConfirm =
    selection === 'all'
      ? failedCount > 0
      : counts
        ? counts[selection] > 0
        : failedCount > 0

  return {
    selection,
    setSelection,
    counts,
    failedCount,
    canConfirm,
    confirmLabel: failedModeConfirmLabel(selection, counts, failedCount),
    confirmArgs: () =>
      selection === 'all'
        ? [null, true]
        : [null, true, failedJobIds, selection],
  }
}
