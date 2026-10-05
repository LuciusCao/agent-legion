import { useState } from 'react'
import type { StudioChatSessionRecord } from './studioChatApi'
import { formatDateTime } from '../../../lib/formatters'
import styles from './StudioChatSessionMenu.module.css'

type Props = {
  session: StudioChatSessionRecord
  pending: boolean
  onSave: (title: string) => void
  onCancel: () => void
}

/** 会话就地改名行（#872；#924 从 StudioChatSessionMenu 拆出，文件预算）：
 * Enter 保存（去首尾空白），Esc 只退出编辑、不关闭菜单。 */
export function StudioChatSessionRenameRow(props: Props) {
  const [draft, setDraft] = useState(props.session.title)
  const save = () => props.onSave(draft.trim())
  return (
    <div className={styles.rowEdit}>
      <input
        className={styles.titleInput}
        aria-label="会话名称"
        value={draft}
        maxLength={200}
        autoFocus
        disabled={props.pending}
        placeholder={`对话 ${formatDateTime(props.session.created_at)}`}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          // Esc 只退出编辑：不冒泡到 Popover（否则整个菜单一起关）。
          if (event.key === 'Escape') {
            event.preventDefault()
            event.stopPropagation()
            props.onCancel()
          } else if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
            event.preventDefault()
            save()
          }
        }}
      />
      <button
        type="button"
        className={styles.textAction}
        disabled={props.pending}
        onClick={save}
      >
        保存
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
