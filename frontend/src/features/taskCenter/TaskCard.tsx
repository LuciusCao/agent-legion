import styles from './TaskCenterPage.module.css'
import {
  estimateRemaining,
  formatDayTime,
  formatDuration,
  type TaskCardModel,
  type TaskCardStatus,
} from './taskCardModel'

const STATUS_LABEL: Record<TaskCardStatus, string> = {
  running: '进行中',
  queued: '排队中',
  paused: '已暂停',
  done: '已完成',
  partial: '部分完成',
  failed: '失败',
  cancelled: '已取消',
}

const KIND_ICON: Record<TaskCardModel['kind'], string> = {
  'run-batch': '＋',
  'job-batch-op': '↻',
}

export type TaskCardActions = {
  onPause?: (id: string) => void
  onResume?: (id: string) => void
  onCancel?: (id: string) => void
  onArchive?: (id: string) => void
  onViewJobs?: (id: string) => void
}

function countLine(card: TaskCardModel): string {
  switch (card.status) {
    case 'queued':
      return `共 ${card.total.toLocaleString()} 条 · 等待开始`
    case 'done':
      return `${card.total.toLocaleString()} / ${card.total.toLocaleString()} 全部完成`
    case 'cancelled':
      return `取消前已处理 ${card.processed.toLocaleString()} / ${card.total.toLocaleString()}`
    case 'failed':
      return `失败前已处理 ${card.processed.toLocaleString()} / ${card.total.toLocaleString()}`
    default:
      return `已处理 ${card.processed.toLocaleString()} / ${card.total.toLocaleString()}`
  }
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
  if (card.archiveSource === 'manual' && card.archivedAt) {
    const actor = card.createdBy || '你'
    return `${formatDayTime(card.archivedAt)} 由 ${actor} 手动归档`
  }
  // auto：归档时间 = 结束时间 + 7 天窗口（查询口径，不落列）。
  if (card.finishedAt) {
    const autoAt = new Date(Date.parse(card.finishedAt) + 7 * 86400000)
    return `${formatDayTime(autoAt.toISOString())} 自动归档（结束超过 7 天）`
  }
  return '自动归档（结束超过 7 天）'
}

/**
 * 任务中心卡片（#1157 phase3）：每卡自洽——类型图标+名称、状态 badge、
 * 进度条、已处理/总数、成功/跳过明细、耗时、操作按钮。source-agnostic
 * （TaskCardModel）；readonly（已归档视图）时不渲染操作按钮。
 */
export function TaskCard({
  card,
  nowMs,
  readonly = false,
  actions = {},
}: {
  card: TaskCardModel
  nowMs: number
  readonly?: boolean
  actions?: TaskCardActions
}) {
  const percent =
    card.total > 0
      ? Math.min(100, Math.round((card.processed / card.total) * 100))
      : 0
  const terminal = card.status === 'done' || card.status === 'partial'
  return (
    <div
      className={`${styles.tcard} ${terminal ? styles.terminal : ''} ${
        readonly ? styles.archivedView : ''
      }`}
      data-testid={`task-card-${card.id}`}
    >
      <div className={styles.tcHead}>
        <span className={`${styles.tcIcon} ${styles[card.kind]}`}>
          {KIND_ICON[card.kind]}
        </span>
        <span className={styles.tcName}>
          {card.title}
          <span className={styles.tcType}>{card.subtitle}</span>
        </span>
        <span className={`${styles.st} ${styles[card.status]}`}>
          <span className={styles.dot} />
          {STATUS_LABEL[card.status]}
        </span>
      </div>
      <div className={styles.tcMeta}>
        {card.refLabel} <span className={styles.mono}>{card.refId}</span>
        {card.startedAt ? ` · ${formatDayTime(card.startedAt)} 发起` : ''}
      </div>
      <div
        className={`${styles.pbar} ${styles[`pbar-${card.status}`]}`}
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className={styles.fill} style={{ width: `${percent}%` }} />
      </div>
      <div className={styles.tcCount}>{countLine(card)}</div>
      <div className={styles.tcDetail}>
        <span className={styles.ok}>
          {card.okLabel} <b>{card.okCount.toLocaleString()}</b>
        </span>
        {card.skippedCount > 0 && (
          <span className={styles.skip}>
            跳过 <b>{card.skippedCount.toLocaleString()}</b>
            {card.skipNote ? `（${card.skipNote}）` : ''}
          </span>
        )}
      </div>
      {card.errorMessage && (
        <div className={styles.tcError}>{card.errorMessage}</div>
      )}
      <div className={styles.tcFoot}>
        <span className={styles.elapsed}>
          {readonly ? archiveLine(card) : footerLine(card, nowMs)}
        </span>
        {!readonly && card.status === 'running' && (
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
        {!readonly && card.status === 'paused' && (
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
        {!readonly && card.status === 'queued' && (
          <button
            className={`${styles.tbtn} ${styles.danger}`}
            onClick={() => actions.onCancel?.(card.id)}
          >
            取消
          </button>
        )}
        {!readonly && (card.status === 'done' || card.status === 'partial') && (
          <>
            <button
              className={styles.tbtn}
              onClick={() => actions.onViewJobs?.(card.id)}
            >
              查看任务
            </button>
            <button
              className={styles.tbtn}
              onClick={() => actions.onArchive?.(card.id)}
            >
              归档
            </button>
          </>
        )}
        {!readonly &&
          (card.status === 'failed' || card.status === 'cancelled') && (
            <>
              {card.okCount > 0 && (
                <button
                  className={styles.tbtn}
                  onClick={() => actions.onViewJobs?.(card.id)}
                >
                  查看任务
                </button>
              )}
              <button
                className={styles.tbtn}
                onClick={() => actions.onArchive?.(card.id)}
              >
                归档
              </button>
            </>
          )}
      </div>
    </div>
  )
}
