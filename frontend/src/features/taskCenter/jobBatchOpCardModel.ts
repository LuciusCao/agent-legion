import type { JobBatchOperation } from '../../api/jobBatchOperationsApi'
import { JOB_BATCH_OP_META, jobBatchOpReasonLabel } from './jobBatchOpMeta'
import type {
  TaskCardModel,
  TaskCardReason,
  TaskCardStatus,
} from './taskCardModel'

/**
 * job_batch_operations（#946，M52）→ 任务中心卡片模型的适配器。与
 * taskCardModel.ts 的 run-batch 适配器姊妹分列，守文件体积预算。
 */

/** op id 是 uuid4 hex（无前缀），短引用取首 8 位（git 短 hash 惯例）。 */
export function shortOpRef(opId: string): string {
  return opId.startsWith('op-') ? opId : `op-${opId.slice(0, 8)}`
}

/** reason_counts → Top N 汇总行（按计数倒序；label 中文化，未收录的码
 * 原样展示）。 */
export function topReasons(
  reasonCounts: Record<string, number> | undefined,
  limit = 3
): TaskCardReason[] {
  return Object.entries(reasonCounts ?? {})
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([code, count]) => ({
      label: jobBatchOpReasonLabel(code),
      count,
    }))
}

/** completed 且有失败记为部分完成；暂停/取消控制面本期不外露
 * （controllable=false，M52 裁决）；归档字段由 M61 的列表端点落地后接入
 * （当前单 op 轮询面无 archived_at）。 */
export function jobBatchOpToCard(op: JobBatchOperation): TaskCardModel {
  const meta = JOB_BATCH_OP_META[op.kind]
  const statusMap: Record<string, TaskCardStatus> = {
    pending: 'queued',
    running: 'running',
    paused: 'paused',
    failed: 'failed',
    cancelled: 'cancelled',
  }
  const status: TaskCardStatus =
    op.status === 'completed'
      ? op.failed_count > 0
        ? 'partial'
        : 'done'
      : (statusMap[op.status] ?? 'queued')
  const firstChunkError = (op.chunk_errors ?? [])[0]
  const downloadUrl =
    typeof op.output?.download_url === 'string' ? op.output.download_url : null
  return {
    kind: 'job-batch-op',
    id: op.id,
    icon: meta.icon,
    title: meta.title,
    subtitle: meta.subtitle,
    status,
    refLabel: '操作',
    refId: shortOpRef(op.id),
    startedAt: op.created_at ?? null,
    finishedAt: op.finished_at ?? null,
    processed: op.processed_count,
    total: op.items_total,
    okCount: op.succeeded_count,
    okLabel: meta.okLabel,
    skippedCount: op.skipped_count,
    skipNote: '',
    failedCount: op.failed_count,
    failedLowerBound: false,
    reasons: topReasons(op.reason_counts),
    downloadUrl,
    controllable: false,
    errorMessage:
      op.error_message ||
      (firstChunkError ? String(firstChunkError.error ?? firstChunkError) : ''),
    createdBy: op.created_by ?? '',
    archivedAt: null,
    archiveSource: null,
  }
}
