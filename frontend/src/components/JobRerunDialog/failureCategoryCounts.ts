import type {
  FailedNodeRunItem,
  FailureCategory,
} from '../../types/failureTypes'

export type FailureCategorySelection = FailureCategory | 'all'

export type FailureCategoryCounts = Record<FailureCategory, number>

export const FAILURE_CATEGORY_ORDER: FailureCategory[] = [
  'technical',
  'business',
  'unknown',
]

export const FAILURE_CATEGORY_LABELS: Record<FailureCategory, string> = {
  technical: '技术性失败',
  business: '业务性失败',
  unknown: '未分类',
}

export const FAILURE_CATEGORY_HINTS: Record<FailureCategory, string> = {
  technical: '超时/断流等，重跑失败节点本身',
  business: '评审不通过，将从上游节点重跑',
  unknown: '建议人工确认后再跑',
}

function normalizeCategory(value: string): FailureCategory {
  return value === 'technical' || value === 'business' ? value : 'unknown'
}

function compareRunRecency(a: FailedNodeRunItem, b: FailedNodeRunItem): number {
  const aTime = a.finished_at ? Date.parse(a.finished_at) : NaN
  const bTime = b.finished_at ? Date.parse(b.finished_at) : NaN
  const aValid = !Number.isNaN(aTime)
  const bValid = !Number.isNaN(bTime)
  if (aValid && bValid && aTime !== bTime) return aTime - bTime
  if (aValid !== bValid) return aValid ? 1 : -1
  return a.node_run_id - b.node_run_id
}

/**
 * 按 job 去重计数：每个 job 归入其最新一条 failed run 的 failure_category。
 * 只统计 jobIds 命中的 job；无法识别的 category 归入 unknown。
 */
export function countJobsByFailureCategory(
  runs: FailedNodeRunItem[],
  jobIds: string[]
): FailureCategoryCounts {
  const counts: FailureCategoryCounts = {
    technical: 0,
    business: 0,
    unknown: 0,
  }
  const wanted = new Set(jobIds)
  const latestByJob = new Map<string, FailedNodeRunItem>()
  for (const run of runs) {
    if (!wanted.has(run.job_id)) continue
    const prev = latestByJob.get(run.job_id)
    if (!prev || compareRunRecency(run, prev) > 0) {
      latestByJob.set(run.job_id, run)
    }
  }
  for (const run of latestByJob.values()) {
    counts[normalizeCategory(run.failure_category)] += 1
  }
  return counts
}

export type FailedModeSummarySource = {
  selection: FailureCategorySelection
  counts: FailureCategoryCounts | null
  failedCount: number
}

export function failedModeSummaryText(
  source: FailedModeSummarySource,
  total: number,
  itemLabel: string
): string {
  const { selection, counts, failedCount } = source
  const prefix = `已选择 ${total} 个${itemLabel}`
  if (selection === 'all') {
    return `${prefix}，其中 ${failedCount} 个失败任务将从各自失败节点重跑`
  }
  const count = counts?.[selection]
  if (count == null) {
    return `${prefix}，${FAILURE_CATEGORY_LABELS[selection]}：${FAILURE_CATEGORY_HINTS[selection]}`
  }
  switch (selection) {
    case 'technical':
      return `${prefix}，其中 ${count} 个含技术性失败，将重跑失败节点本身`
    case 'business':
      return `${prefix}，其中 ${count} 个含业务性失败，将从上游节点重跑，失败节点随后自动重跑`
    case 'unknown':
      return `${prefix}，其中 ${count} 个含未分类失败，建议人工确认后再跑`
  }
}

export function failedModeConfirmLabel(
  selection: FailureCategorySelection,
  counts: FailureCategoryCounts | null,
  failedCount: number
): string {
  if (selection === 'all') return `重跑 ${failedCount} 个失败任务`
  const count = counts?.[selection]
  const label = FAILURE_CATEGORY_LABELS[selection]
  return count == null ? `重跑${label}` : `重跑 ${count} 个${label}`
}
