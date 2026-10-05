import type { ReactNode } from 'react'
import styles from './StudioChatArchivedSessions.module.css'
import { retentionNotice } from './studioChatRetention'

type Props = {
  /** danger = 不可恢复的删除（红色）；primary = 可恢复的归档（主色）。 */
  tone: 'danger' | 'primary'
  text: ReactNode
  confirmLabel: string
  pending: boolean
  onConfirm: () => void
  onCancel: () => void
}

/** 会话菜单的行内二次确认（#872 删除 / #924 归档共用）。 */
export function StudioChatSessionRowConfirm(props: Props) {
  return (
    <div className={styles.rowConfirm} data-tone={props.tone} role="alert">
      <span className={styles.confirmText}>{props.text}</span>
      <button
        type="button"
        className={
          props.tone === 'danger' ? styles.dangerAction : styles.primaryAction
        }
        disabled={props.pending}
        onClick={props.onConfirm}
      >
        {props.confirmLabel}
      </button>
      <button
        type="button"
        className={styles.textAction}
        disabled={props.pending}
        onClick={props.onCancel}
      >
        取消
      </button>
    </div>
  )
}

/** 删除确认文案（#924）：明确不可恢复，并指向可恢复的归档。
 * #1041：配置了对话保留策略时附「将于 N 天后自动清理」（物理清除时点）。 */
export function deleteConfirmText(
  label: string,
  live: boolean,
  retentionDays = 0
): string {
  const purge = retentionNotice(retentionDays)
  return `永久删除「${label}」？${live ? '会先关闭运行中的会话，' : ''}删除后不可恢复${purge ? `，数据${purge}` : ''}；只想收起会话请改用归档`
}

/** #924：删除是次要 danger 操作（红色），归档是主整理操作（悬停主色）。
 * 用 sx 而非 CSS module：MUI IconButton 的默认色会盖过模块类名。 */
export const DANGER_ICON_SX = {
  color: '#e57373',
  '&:hover': { color: '#c62828' },
} as const
export const ARCHIVE_ICON_SX = { '&:hover': { color: '#1565c0' } } as const
