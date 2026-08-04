import { useMemo, useState } from 'react'
import { fetchFailedNodeRuns } from '../../api'
import { useAsync } from '../../hooks/useAsync'
import type { JobSummary } from '../../types'
import type { FailureCategory } from '../../types/failureTypes'
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
  fromNodeKey?: string,
]

export type FailureCategoryState = {
  selection: FailureCategorySelection
  setSelection: (value: FailureCategorySelection) => void
  fromNodeKey: string | null
  setFromNodeKey: (value: string | null) => void
  counts: FailureCategoryCounts | null
  failedCount: number
  canConfirm: boolean
  confirmLabel: string
  confirmArgs: () => JobRerunConfirmArgs
}

/**
 * 失败类别子选项状态：failedMode 激活时懒加载类别计数，
 * 加载失败静默降级为不显示计数（counts 保持 null）。
 */
export function useFailureCategories(
  failedMode: boolean,
  failureContext: FailureCategoryContext | undefined,
  failedJobs: JobSummary[]
): FailureCategoryState {
  const [selection, setSelection] = useState<FailureCategorySelection>('all')
  const [fromNodeKey, setFromNodeKey] = useState<string | null>(null)

  const workspaceId = failureContext?.workspaceId
  const workflowKey = failureContext?.workflowKey

  // 加载失败时 error 不消费：chips 保持可见但不显示计数（静默降级）。
  const { data: failedRuns } = useAsync(
    async () => {
      const data = await fetchFailedNodeRuns(workspaceId ?? '', {
        workflowKey,
      })
      return data.runs ?? []
    },
    [failedMode, workspaceId, workflowKey],
    { enabled: failedMode && !!workspaceId }
  )

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
    fromNodeKey,
    setFromNodeKey,
    counts,
    failedCount,
    canConfirm,
    confirmLabel: failedModeConfirmLabel(selection, counts, failedCount),
    confirmArgs: () => {
      if (selection === 'all') return [null, true]
      const args: JobRerunConfirmArgs = [null, true, failedJobIds, selection]
      // 指定起始节点时追加（仅具体失败类型支持；'all' 维持原语义）。
      if (fromNodeKey) args.push(fromNodeKey)
      return args
    },
  }
}
