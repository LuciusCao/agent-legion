import styles from './TaskCenterPage.module.css'
import { MaterialIcon } from '../../components/MaterialIcon'
import {
  formatDayTime,
  type TaskCardModel,
  type TaskCardStatus,
} from './taskCardModel'
import { TaskCardFooter, type TaskCardActions } from './TaskCardFooter'
import { TaskCardGate } from './TaskCardGate'

export type { TaskCardActions } from './TaskCardFooter'

const STATUS_LABEL: Record<TaskCardStatus, string> = {
  running: '进行中',
  queued: '排队中',
  paused: '已暂停',
  done: '已完成',
  partial: '部分完成',
  failed: '失败',
  cancelled: '已取消',
}

const COUNT_PREFIX: Partial<Record<TaskCardStatus, string>> = {
  paused: '暂停前已处理',
  cancelled: '取消前已处理',
  failed: '失败前已处理',
}

function countLine(card: TaskCardModel): string {
  if (card.selectionComplete === false) {
    const known = `已知 ${card.total.toLocaleString()}+ 条`
    const prefix = COUNT_PREFIX[card.status] ?? '已处理'
    const suffix = card.status === 'running' ? '仍在计算' : '选择尚未完成'
    return `${prefix} ${card.processed.toLocaleString()} · ${known}（${suffix}）`
  }
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

/**
 * 任务中心卡片（#1157 phase3）：每卡自洽——类型图标+名称、状态 badge、
 * 进度条、已处理/总数、成功/跳过/失败明细（#946 附原因分布与打包下载）、
 * 耗时、操作按钮（底行拆至 TaskCardFooter）。source-agnostic
 * （TaskCardModel）；readonly（已归档视图）时不渲染操作按钮。
 */
export function TaskCard({
  card,
  nowMs,
  readonly = false,
  workspacePaused = false,
  actions = {},
}: {
  card: TaskCardModel
  nowMs: number
  readonly?: boolean
  workspacePaused?: boolean
  actions?: TaskCardActions
}) {
  const selecting =
    card.selectionComplete === false && card.status === 'running'
  const percent =
    Math.min(100, Math.round((card.processed / card.total) * 100)) || 0
  const terminal = card.status === 'done' || card.status === 'partial'
  const displayStatus = selecting ? 'selecting' : card.status
  const text = selecting ? '选择计算中' : STATUS_LABEL[card.status]
  const width = card.selectionComplete === false ? undefined : `${percent}%`
  return (
    <div
      className={`${styles.tcard} ${terminal ? styles.terminal : ''} ${
        readonly ? styles.archivedView : ''
      }`}
      data-testid={`task-card-${card.id}`}
    >
      <div className={styles.tcHead}>
        <span className={`${styles.tcIcon} ${styles[card.kind]}`}>
          <MaterialIcon name={card.icon} style={{ fontSize: 18 }} />
        </span>
        <span className={styles.tcName}>
          {card.title}
          <span className={styles.tcType}>{card.subtitle}</span>
        </span>
        <span className={`${styles.st} ${styles[card.status]}`}>
          <span className={styles.dot} />
          {text}
        </span>
      </div>
      <TaskCardGate card={card} workspacePaused={workspacePaused} />
      <div className={styles.tcMeta}>
        {card.refLabel} <span className={styles.mono}>{card.refId}</span>
        {card.startedAt ? ` · ${formatDayTime(card.startedAt)} 发起` : ''}
      </div>
      <div
        className={`${styles.pbar} ${styles[`pbar-${displayStatus}`]}`}
        role="progressbar"
        aria-label={selecting ? '正在计算选择集，总量未定' : undefined}
        aria-valuetext={card.selectionComplete === false ? text : undefined}
        aria-valuenow={card.selectionComplete === false ? undefined : percent}
      >
        {(card.selectionComplete !== false || selecting) && (
          <div className={styles.fill} style={{ width }} />
        )}
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
        {card.failedCount > 0 && (
          <span className={styles.fail}>
            失败{' '}
            <b>
              {card.failedLowerBound ? '≥' : ''}
              {card.failedCount.toLocaleString()}
            </b>
          </span>
        )}
        {card.reasons.map((reason) => (
          <span key={reason.label} className={styles.reason}>
            {reason.label} <b>{reason.count.toLocaleString()}</b>
          </span>
        ))}
      </div>
      {card.errorMessage && (
        <div className={styles.tcError}>{card.errorMessage}</div>
      )}
      <TaskCardFooter
        card={card}
        nowMs={nowMs}
        readonly={readonly}
        actions={actions}
      />
    </div>
  )
}
