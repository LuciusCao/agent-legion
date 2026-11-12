import type { RunBatchListItem } from '../../api/runBatchesApi'
import type { FeedingGateCardModel } from './taskCardGateModel'

// job-batch-op（#946）适配器姊妹分列在 jobBatchOpCardModel.ts（体积预算），
// 此处 re-export 保持既有 import 路径稳定。
export { jobBatchOpToCard, shortOpRef, topReasons } from './jobBatchOpCardModel'

/**
 * 任务中心卡片数据模型（#1157 phase3）——source-agnostic：run_batches
 * （异步添加条目）与 job_batch_operations（#946 多选批量操作，M52）共用
 * 同一模型与 TaskCard 组件，各自提供 adapter。
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

/** 失败/跳过原因的汇总行（reason_counts 的 Top N，label 已中文化）。 */
export type TaskCardReason = {
  label: string
  count: number
}

export type TaskCardModel = FeedingGateCardModel & {
  kind: TaskCardKind
  /** run_batches 视角下即 run_id（控制面与归档的操作键）。 */
  id: string
  /** Material Symbols 图标名（MaterialIcon；视觉纪律禁 emoji）。 */
  icon: string
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
  /** chunk 级失败条目数（后端由 chunk_errors 推导；lower_bound 时渲染 ≥）。 */
  failedCount: number
  failedLowerBound: boolean
  /** 失败/跳过原因 Top 分布（job-batch-op 的 reason_counts），空不渲染。 */
  reasons: TaskCardReason[]
  /** package 终态的 zip 下载地址（output.download_url），有则渲染下载按钮。 */
  downloadUrl: string | null
  /** 进行中是否暴露暂停/继续/取消控制入口。 */
  controllable: boolean
  errorMessage: string
  createdBy: string
  archivedAt: string | null
  archiveSource: 'manual' | 'auto' | null
}

/** mono 引用取 run id 尾段 digest——run id 前段是 workspace/workflow 前缀，
 * 同 workspace 内无区分度（reviewer r1 P2-5）。 */
export function shortRunRef(runId: string): string {
  return runId.startsWith('run-') ? runId : `run-${runId.slice(-8)}`
}

/** run_batches 列表项 → 卡片模型。跳过数 = 已处理 − 新建 − chunk 失败
 * （reviewer r1 P2-1：chunk 容错跳过的条目不是「已有任务」，拆为独立
 * 失败计数）；completed 且有 chunk 错误记为部分完成。 */
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
  const failedCount = item.failed_item_count ?? 0
  return {
    kind: 'run-batch',
    id: item.run_id,
    icon: 'add_task',
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
    skippedCount: Math.max(
      0,
      item.processed_count - item.jobs_created - failedCount
    ),
    skipNote: '已有任务',
    failedCount,
    failedLowerBound: item.failed_item_count_lower_bound ?? false,
    reasons: [],
    downloadUrl: null,
    controllable: true,
    errorMessage,
    createdBy: item.created_by,
    archivedAt: item.archived_at,
    archiveSource: item.archive_source ?? null,
    feedingGated: item.feeding_gated ?? false,
    workspaceBacklog: item.workspace_backlog ?? null,
    watermark: item.watermark ?? null,
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
