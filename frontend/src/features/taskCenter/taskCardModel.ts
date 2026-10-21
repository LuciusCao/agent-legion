import type { RunBatchListItem } from '../../api/runBatchesApi'

/**
 * 任务中心卡片数据模型（#1157 phase3）——source-agnostic：run_batches
 * （异步添加条目）与后续 job_batch_operations（M52 多选批量操作）共用同一
 * 模型与 TaskCard 组件，各自提供 adapter。本切片只接 run-batch。
 */
export type TaskCardKind = 'run-batch' | 'job-batch-op'

export type TaskCardStatus =
  | 'running'
  | 'queued'
  | 'paused'
  | 'done'
  | 'partial'
  | 'failed'
  | 'cancelled'

export type TaskCardModel = {
  kind: TaskCardKind
  /** run_batches 视角下即 run_id（控制面与归档的操作键）。 */
  id: string
  title: string
  subtitle: string
  status: TaskCardStatus
  /** 「运行」/「批次」+ 短 id（mono 展示）。 */
  refLabel: string
  refId: string
  startedAt: string | null
  finishedAt: string | null
  processed: number
  total: number
  okCount: number
  okLabel: string
  skippedCount: number
  /** 跳过的补充说明（如「已有任务」），无则不渲染括号。 */
  skipNote: string
  errorMessage: string
  createdBy: string
  archivedAt: string | null
  archiveSource: 'manual' | 'auto' | null
}

export function shortRunRef(runId: string): string {
  return runId.startsWith('run-') ? runId : `run-${runId.slice(0, 8)}`
}

/** run_batches 列表项 → 卡片模型。跳过数 = 已处理 − 新建（后端契约：dedup
 * 过滤与 chunk 跳过同源，不单独物化）；completed 且有 chunk 错误记为
 * 部分完成。 */
export function runBatchToCard(item: RunBatchListItem): TaskCardModel {
  const chunkErrors = item.chunk_errors?.length ?? 0
  const statusMap: Record<string, TaskCardStatus> = {
    pending: 'queued',
    running: 'running',
    paused: 'paused',
    failed: 'failed',
    cancelled: 'cancelled',
  }
  const status: TaskCardStatus =
    item.status === 'completed'
      ? chunkErrors > 0
        ? 'partial'
        : 'done'
      : (statusMap[item.status] ?? 'queued')
  const firstChunkError = chunkErrors > 0 ? item.chunk_errors[0] : null
  const errorMessage =
    item.error_message ||
    (firstChunkError ? String(firstChunkError.error ?? firstChunkError) : '')
  return {
    kind: 'run-batch',
    id: item.run_id,
    title: '批量创建',
    subtitle: '添加条目',
    status,
    refLabel: '运行',
    refId: shortRunRef(item.run_id),
    startedAt: item.created_at,
    finishedAt: item.finished_at,
    processed: item.processed_count,
    total: item.items_total,
    okCount: item.jobs_created,
    okLabel: '新建',
    skippedCount: Math.max(0, item.processed_count - item.jobs_created),
    skipNote: '已有任务',
    errorMessage,
    createdBy: item.created_by,
    archivedAt: item.archived_at,
    archiveSource: item.archive_source ?? null,
  }
}

const MINUTE = 60
const HOUR = 3600

/** 「48 秒」「3 分 12 秒」「1 小时 02 分」。 */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds))
  if (seconds >= HOUR) {
    const hours = Math.floor(seconds / HOUR)
    const minutes = Math.floor((seconds % HOUR) / MINUTE)
    return `${hours} 小时 ${String(minutes).padStart(2, '0')} 分`
  }
  if (seconds >= MINUTE) {
    const minutes = Math.floor(seconds / MINUTE)
    const rest = seconds % MINUTE
    return rest > 0 ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`
  }
  return `${seconds} 秒`
}

/** 「预计剩余约 2 分钟」（按当前速率线性外推，>90s 取整到分钟）。 */
export function estimateRemaining(
  processed: number,
  total: number,
  elapsedSeconds: number
): string | null {
  if (processed <= 0 || total <= processed || elapsedSeconds <= 0) return null
  const remaining = (elapsedSeconds * (total - processed)) / processed
  if (remaining >= 90)
    return `预计剩余约 ${Math.round(remaining / MINUTE)} 分钟`
  return `预计剩余约 ${Math.max(1, Math.round(remaining))} 秒`
}

/** 「今天 10:21」/「昨天 17:40」/「10月14日」。 */
export function formatDayTime(iso: string, now: Date = new Date()): string {
  const date = new Date(iso)
  const hhmm = `${String(date.getHours()).padStart(2, '0')}:${String(
    date.getMinutes()
  ).padStart(2, '0')}`
  const startOfDay = (d: Date) =>
    new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const dayDiff = Math.round((startOfDay(now) - startOfDay(date)) / 86400000)
  if (dayDiff <= 0) return `今天 ${hhmm}`
  if (dayDiff === 1) return `昨天 ${hhmm}`
  return `${date.getMonth() + 1}月${date.getDate()}日`
}
