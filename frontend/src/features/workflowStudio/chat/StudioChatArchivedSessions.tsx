import { useState } from 'react'
import { IconButton, Tooltip } from '@mui/material'
import {
  ChevronRight,
  DeleteOutline,
  ExpandMore,
  UnarchiveOutlined,
} from '@mui/icons-material'
import type { StudioChatSessionRecord } from './studioChatApi'
import { sessionLabel } from './studioChatSessionLabel'
import {
  StudioChatSessionRowConfirm,
  deleteConfirmText,
} from './StudioChatSessionRowConfirm'
import { DANGER_ICON_SX } from './StudioChatSessionRowConfirm'
import styles from './StudioChatArchivedSessions.module.css'

type Props = {
  sessions: StudioChatSessionRecord[]
  pending: boolean
  /** 会话菜单的统一执行器：pending / 行内错误展示都在菜单那一层。 */
  run: (action: () => Promise<void>) => Promise<boolean>
  onUnarchive: (sessionId: string) => Promise<void>
  onDelete?: (sessionId: string) => Promise<void>
}

/** 会话菜单底部的「已归档（N）」折叠区（#924）：默认收起；展开后每条
 * 归档会话可「恢复」（取消归档，回到列表、仍为已关闭，按「继续对话」
 * 恢复运行）或永久删除（danger + 行内二次确认）。归档会话不可直接选中。 */
export function StudioChatArchivedSessions(props: Props) {
  const [open, setOpen] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  if (props.sessions.length === 0) return null

  function renderRow(session: StudioChatSessionRecord) {
    const label = sessionLabel(session)
    if (confirmDelete === session.id && props.onDelete) {
      const onDelete = props.onDelete
      return (
        <StudioChatSessionRowConfirm
          tone="danger"
          text={deleteConfirmText(label, false)}
          confirmLabel="永久删除"
          pending={props.pending}
          onConfirm={() =>
            void props
              .run(() => onDelete(session.id))
              .then((ok) => ok && setConfirmDelete(null))
          }
          onCancel={() => setConfirmDelete(null)}
        />
      )
    }
    return (
      <div className={styles.archivedRow}>
        <span className={styles.archivedLabel} title={label}>
          {label}
        </span>
        <button
          type="button"
          className={styles.restoreAction}
          aria-label={`恢复会话 ${label}`}
          disabled={props.pending}
          onClick={() => void props.run(() => props.onUnarchive(session.id))}
        >
          <UnarchiveOutlined fontSize="inherit" />
          恢复
        </button>
        {props.onDelete && (
          <Tooltip title="永久删除">
            <IconButton
              size="small"
              sx={DANGER_ICON_SX}
              aria-label={`删除会话 ${label}`}
              onClick={() => setConfirmDelete(session.id)}
            >
              <DeleteOutline fontSize="inherit" />
            </IconButton>
          </Tooltip>
        )}
      </div>
    )
  }

  return (
    <div className={styles.section}>
      <button
        type="button"
        className={styles.toggle}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? (
          <ExpandMore fontSize="inherit" />
        ) : (
          <ChevronRight fontSize="inherit" />
        )}
        已归档（{props.sessions.length}）
      </button>
      {open && (
        <ul className={styles.list} aria-label="已归档会话">
          {props.sessions.map((session) => (
            <li key={session.id}>{renderRow(session)}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
