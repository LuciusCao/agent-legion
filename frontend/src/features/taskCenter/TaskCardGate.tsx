import type { FeedingGateCardModel } from './taskCardGateModel'
import styles from './TaskCardGate.module.css'

export function TaskCardGate({
  card,
  workspacePaused,
}: {
  card: FeedingGateCardModel
  workspacePaused: boolean
}) {
  if (!card.feedingGated) return null
  const amounts =
    card.workspaceBacklog !== null && card.watermark !== null
      ? `当前任务积压 ${card.workspaceBacklog.toLocaleString()}，水位 ${card.watermark.toLocaleString()}。`
      : '当前任务积压已达到处理水位。'
  const continuation = '积压降至水位以下后会自动继续。'
  const explanation = workspacePaused
    ? `调度已暂停；${amounts}恢复调度且${continuation}`
    : `${amounts}${continuation}`
  return (
    <div className={styles.feedingGate} data-testid="feeding-gate-note">
      <span className={styles.feedingGateBadge} title={explanation}>
        水位等待
      </span>
      <span className={styles.feedingGateText}>{explanation}</span>
    </div>
  )
}
