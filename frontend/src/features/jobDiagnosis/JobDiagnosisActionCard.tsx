import {
  suggestionTitle,
  type JobActionSuggestion,
} from './jobDiagnosisContext'
import styles from './JobDiagnosisPanel.module.css'

export type ActionCardState =
  | { phase: 'pending' }
  | { phase: 'executing' }
  | { phase: 'done' }
  | { phase: 'failed'; error: string }
  | { phase: 'dismissed' }

type Props = {
  suggestion: JobActionSuggestion
  state: ActionCardState
  onConfirm: (suggestion: JobActionSuggestion) => void
  onDismiss: (suggestion: JobActionSuggestion) => void
}

/** 建议动作确认卡片（#329）：agent 只产出建议 payload，执行永远由人点击，
 * 且经宿主会话走常规 job 路由（scoped token 碰动作端点是 403）。 */
export function JobDiagnosisActionCard(props: Props) {
  const { suggestion, state } = props
  const title = suggestionTitle(suggestion)
  return (
    <div
      className={styles.actionCard}
      role="group"
      aria-label={`建议动作 ${title}`}
    >
      <div className={styles.actionTitle}>
        {state.phase === 'pending' && (
          <span className={styles.actionBadge}>需要你的确认</span>
        )}
        建议动作：{title}
      </div>
      {suggestion.reason && state.phase !== 'dismissed' && (
        <div className={styles.actionReason}>{suggestion.reason}</div>
      )}
      {(state.phase === 'pending' || state.phase === 'failed') && (
        <div className={styles.actionButtons}>
          <button
            type="button"
            className={styles.actionConfirm}
            onClick={() => props.onConfirm(suggestion)}
          >
            {state.phase === 'failed' ? '重试执行' : '确认执行'}
          </button>
          <button
            type="button"
            className={styles.actionDismiss}
            onClick={() => props.onDismiss(suggestion)}
          >
            忽略
          </button>
        </div>
      )}
      {state.phase === 'failed' && (
        <div className={styles.actionFailed} role="alert">
          执行失败：{state.error}
        </div>
      )}
      {state.phase === 'executing' && (
        <div className={styles.actionResolved}>执行中…</div>
      )}
      {state.phase === 'done' && (
        <div className={styles.actionResolved}>
          已执行，页面数据刷新后可直接验证
        </div>
      )}
      {state.phase === 'dismissed' && (
        <div className={styles.actionResolved}>已忽略</div>
      )}
    </div>
  )
}
