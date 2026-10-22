import styles from './TaskCenterPage.module.css'
import {
  estimateRemaining,
  formatDayTime,
  formatDuration,
  type TaskCardModel,
} from './taskCardModel'

export type TaskCardActions = {
  onPause?: (id: string) => void
  onResume?: (id: string) => void
  onCancel?: (id: string) => void
  onArchive?: (id: string) => void
  onViewJobs?: (id: string) => void
  /** job-batch-op 的逐 job 结果查看（/results keyset 分页）。 */
  onViewResults?: (id: string) => void
}

function footerLine(card: TaskCardModel, nowMs: number): string {
  const startedMs = card.startedAt ? Date.parse(card.startedAt) : null
  const finishedMs = card.finishedAt ? Date.parse(card.finishedAt) : null
  switch (card.status) {
    case 'running': {
      const elapsed = startedMs ? (nowMs - startedMs) / 1000 : 0
      const eta = estimateRemaining(card.processed, card.total, elapsed)
      return `已进行 ${formatDuration(elapsed)}${eta ? ` · ${eta}` : ''}`
    }
    case 'paused':
      return '已暂停 · 后台处理已挂起，已处理部分保留'
    case 'queued':
      return startedMs
        ? `等待 ${formatDuration((nowMs - startedMs) / 1000)}`
        : '排队等待中'
    case 'done':
    case 'partial': {
      const cost =
        startedMs && finishedMs
          ? `耗时 ${formatDuration((finishedMs - startedMs) / 1000)} · `
          : ''
      const when = card.finishedAt ? formatDayTime(card.finishedAt) : ''
      return `${cost}${when} ${card.status === 'done' ? '完成' : '结束'}`
    }
    case 'failed':
      return `${card.finishedAt ? formatDayTime(card.finishedAt) : ''} 中断`
    case 'cancelled':
      return `${card.finishedAt ? formatDayTime(card.finishedAt) : ''} 取消 · 已处理部分保留，不受影响`
  }
}

function archiveLine(card: TaskCardModel): string {
  // 不署名：created_by 是提交者而非归档者，多操作者场景会署错名
  // （reviewer r1 P2-2）；archived_by 列属 follow-up，落地后再补归属。
  if (card.archiveSource === 'manual' && card.archivedAt) {
    return `${formatDayTime(card.archivedAt)} 手动归档`
  }
  // auto：归档时间 = 结束时间 + 7 天窗口（查询口径，不落列）。
  if (card.finishedAt) {
    const autoAt = new Date(Date.parse(card.finishedAt) + 7 * 86400000)
    return `${formatDayTime(autoAt.toISOString())} 自动归档（结束超过 7 天）`
  }
  return '自动归档（结束超过 7 天）'
}

/**
 * 卡片底行（从 TaskCard 拆出守体积预算）：左侧耗时/归档口径，右侧状态相关
 * 操作按钮。暂停/继续/取消只出现在 controllable 卡片（run_batches）；
 * job-batch-op 的 pause/cancel 控制面本期不外露（M52 裁决）。终态动作按
 * 调用方提供的 handler 渲染（归档端点由 M61 落地后接入）。
 */
export function TaskCardFooter({
  card,
  nowMs,
  readonly,
  actions,
}: {
  card: TaskCardModel
  nowMs: number
  readonly: boolean
  actions: TaskCardActions
}) {
  const terminal = card.status === 'done' || card.status === 'partial'
  return (
    <div className={styles.tcFoot}>
      <span className={styles.elapsed}>
        {readonly ? archiveLine(card) : footerLine(card, nowMs)}
      </span>
      {!readonly && card.downloadUrl && terminal && (
        <a
          className={styles.tbtn}
          href={card.downloadUrl}
          target="_blank"
          rel="noreferrer"
        >
          下载打包
        </a>
      )}
      {!readonly && card.controllable && card.status === 'running' && (
        <>
          <button
            className={styles.tbtn}
            onClick={() => actions.onPause?.(card.id)}
          >
            暂停
          </button>
          <button
            className={`${styles.tbtn} ${styles.danger}`}
            onClick={() => actions.onCancel?.(card.id)}
          >
            取消
          </button>
        </>
      )}
      {!readonly && card.controllable && card.status === 'paused' && (
        <>
          <button
            className={styles.tbtn}
            onClick={() => actions.onResume?.(card.id)}
          >
            继续
          </button>
          <button
            className={`${styles.tbtn} ${styles.danger}`}
            onClick={() => actions.onCancel?.(card.id)}
          >
            取消
          </button>
        </>
      )}
      {!readonly && card.controllable && card.status === 'queued' && (
        <button
          className={`${styles.tbtn} ${styles.danger}`}
          onClick={() => actions.onCancel?.(card.id)}
        >
          取消
        </button>
      )}
      {!readonly && terminal && (
        <>
          {card.kind === 'job-batch-op' && actions.onViewResults && (
            <button
              className={styles.tbtn}
              onClick={() => actions.onViewResults?.(card.id)}
            >
              逐条结果
            </button>
          )}
          {actions.onViewJobs && (
            <button
              className={styles.tbtn}
              onClick={() => actions.onViewJobs?.(card.id)}
            >
              查看任务
            </button>
          )}
          {actions.onArchive && (
            <button
              className={styles.tbtn}
              onClick={() => actions.onArchive?.(card.id)}
            >
              归档
            </button>
          )}
        </>
      )}
      {!readonly &&
        (card.status === 'failed' || card.status === 'cancelled') && (
          <>
            {card.kind === 'job-batch-op' && actions.onViewResults && (
              <button
                className={styles.tbtn}
                onClick={() => actions.onViewResults?.(card.id)}
              >
                逐条结果
              </button>
            )}
            {card.okCount > 0 && actions.onViewJobs && (
              <button
                className={styles.tbtn}
                onClick={() => actions.onViewJobs?.(card.id)}
              >
                查看任务
              </button>
            )}
            {actions.onArchive && (
              <button
                className={styles.tbtn}
                onClick={() => actions.onArchive?.(card.id)}
              >
                归档
              </button>
            )}
          </>
        )}
    </div>
  )
}
