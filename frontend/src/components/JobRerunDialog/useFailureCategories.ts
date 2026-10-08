import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchFailedNodeRuns } from '../../api'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
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

/** 计数最多翻这么多页（每页服务端默认 500 条）；超出视为无法给出准确计数。 */
export const MAX_FAILED_RUN_PAGES = 20

/**
 * 沿 next_cursor 翻页拉取 failed runs（#713：服务端单页有界）。超过页数上限
 * 返回 null——计数不完整时宁可静默降级为不显示计数，也不显示偏小的数字。
 */
async function fetchFailedRunPages(
  workspaceId: string
): Promise<FailedNodeRunItem[] | null> {
  const runs: FailedNodeRunItem[] = []
  let cursor: string | undefined
  for (let page = 0; page < MAX_FAILED_RUN_PAGES; page += 1) {
    const data = cursor
      ? await fetchFailedNodeRuns(workspaceId, cursor)
      : await fetchFailedNodeRuns(workspaceId)
    runs.push(...(data.runs ?? []))
    if (!data.next_cursor) return runs
    cursor = data.next_cursor
  }
  return null
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

  // 加载失败时 error 不消费：chips 保持可见但不显示计数（静默降级）。
  const { data: failedRuns } = useQuery({
    queryKey: extraQueryKeys.failedNodeRuns(workspaceId ?? ''),
    queryFn: () => fetchFailedRunPages(workspaceId ?? ''),
    enabled: failedMode && !!workspaceId,
  })

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
