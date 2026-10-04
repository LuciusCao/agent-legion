import { useState } from 'react'
import { IconButton, Popover, Tooltip } from '@mui/material'
import {
  ArchiveOutlined,
  DeleteOutline,
  EditOutlined,
  ExpandMore,
} from '@mui/icons-material'
import type { StudioChatSessionRecord } from './studioChatApi'
import { StudioChatArchivedSessions } from './StudioChatArchivedSessions'
import { StudioChatSessionRenameRow } from './StudioChatSessionRenameRow'
import {
  StudioChatSessionRowConfirm,
  deleteConfirmText,
} from './StudioChatSessionRowConfirm'
import { sessionLabel } from './studioChatSessionLabel'
import { ARCHIVE_ICON_SX, DANGER_ICON_SX } from './StudioChatSessionRowConfirm'
import styles from './StudioChatSessionMenu.module.css'

type Props = {
  sessions: StudioChatSessionRecord[]
  activeSessionId: string | null
  onSelectSession: (sessionId: string) => void
  /** 会话管理动作（#872）；不传则菜单只做选择。失败经 reject 回传，行内展示。 */
  onRename?: (sessionId: string, title: string) => Promise<void>
  onDelete?: (sessionId: string) => Promise<void>
  /** 归档（#924）：主整理操作，可恢复；归档会话进底部「已归档（N）」区。 */
  archivedSessions?: StudioChatSessionRecord[]
  onArchive?: (sessionId: string) => Promise<void>
  onUnarchive?: (sessionId: string) => Promise<void>
}

type Mode = {
  kind: 'rename' | 'delete' | 'archive'
  sessionId: string
} | null

/** 会话选择器（#872）：原生 select 换成按钮 + 弹出列表，列表项右侧挂
 * 改名（就地编辑：Enter 保存 / Esc 取消）、归档（#924 主操作：已关闭的
 * 直接归档，运行中的先行内确认「会先关闭」）与删除（次要 danger 操作，
 * 行内二次确认「不可恢复」）。底部「已归档（N）」折叠区可恢复归档会话。 */
export function StudioChatSessionMenu(props: Props) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const [mode, setMode] = useState<Mode>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const active = props.sessions.find((row) => row.id === props.activeSessionId)

  function close() {
    setAnchor(null)
    setMode(null)
    setError(null)
  }

  async function run(action: () => Promise<void>): Promise<boolean> {
    setPending(true)
    setError(null)
    try {
      await action()
      setMode(null)
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败')
      return false
    } finally {
      setPending(false)
    }
  }

  function renderRow(session: StudioChatSessionRecord) {
    const label = sessionLabel(session)
    const closed = session.status === 'closed' || session.status === 'error'
    if (mode?.sessionId === session.id && mode.kind === 'rename') {
      return (
        <StudioChatSessionRenameRow
          session={session}
          pending={pending}
          onSave={(title) => void run(() => props.onRename!(session.id, title))}
          onCancel={() => setMode(null)}
        />
      )
    }
    if (mode?.sessionId === session.id && mode.kind === 'delete') {
      return (
        <StudioChatSessionRowConfirm
          tone="danger"
          text={deleteConfirmText(label, !closed)}
          confirmLabel="永久删除"
          pending={pending}
          onConfirm={() => void run(() => props.onDelete!(session.id))}
          onCancel={() => setMode(null)}
        />
      )
    }
    if (mode?.sessionId === session.id && mode.kind === 'archive') {
      return (
        <StudioChatSessionRowConfirm
          tone="primary"
          text={`归档「${label}」？会先关闭运行中的会话，之后可在「已归档」中恢复`}
          confirmLabel="归档"
          pending={pending}
          onConfirm={() => void run(() => props.onArchive!(session.id))}
          onCancel={() => setMode(null)}
        />
      )
    }
    return (
      <div className={styles.row} data-active={session.id === active?.id}>
        <button
          type="button"
          className={styles.rowMain}
          onClick={() => {
            props.onSelectSession(session.id)
            close()
          }}
        >
          <span className={styles.rowLabel}>{label}</span>
          {closed && <span className={styles.closedTag}>已关闭</span>}
        </button>
        {props.onRename && (
          <Tooltip title="重命名">
            <IconButton
              size="small"
              aria-label={`重命名会话 ${label}`}
              onClick={() => {
                setError(null)
                setMode({ kind: 'rename', sessionId: session.id })
              }}
            >
              <EditOutlined fontSize="inherit" />
            </IconButton>
          </Tooltip>
        )}
        {props.onArchive && (
          <Tooltip title="归档（可恢复）">
            <IconButton
              size="small"
              sx={ARCHIVE_ICON_SX}
              aria-label={`归档会话 ${label}`}
              disabled={pending}
              onClick={() => {
                setError(null)
                // 已关闭的会话直接归档（可恢复，无需确认）；运行中的会先
                // 被关闭，行内确认一次。
                if (closed) void run(() => props.onArchive!(session.id))
                else setMode({ kind: 'archive', sessionId: session.id })
              }}
            >
              <ArchiveOutlined fontSize="inherit" />
            </IconButton>
          </Tooltip>
        )}
        {props.onDelete && (
          <Tooltip title="永久删除">
            <IconButton
              size="small"
              sx={DANGER_ICON_SX}
              aria-label={`删除会话 ${label}`}
              onClick={() => {
                setError(null)
                setMode({ kind: 'delete', sessionId: session.id })
              }}
            >
              <DeleteOutline fontSize="inherit" />
            </IconButton>
          </Tooltip>
        )}
      </div>
    )
  }

  return (
    <>
      <button
        type="button"
        className={styles.trigger}
        aria-label="选择会话"
        aria-haspopup="dialog"
        aria-expanded={anchor !== null}
        title={active ? sessionLabel(active) : undefined}
        onClick={(event) => setAnchor(event.currentTarget)}
      >
        <span className={styles.triggerLabel}>
          {active ? sessionLabel(active) : '未选择会话'}
          {active?.status === 'closed' ? '（已关闭）' : ''}
        </span>
        <ExpandMore fontSize="inherit" />
      </button>
      <Popover
        open={anchor !== null}
        anchorEl={anchor}
        onClose={close}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        slotProps={{ paper: { className: styles.popover } }}
      >
        {props.sessions.length === 0 ? (
          <div className={styles.empty}>暂无会话</div>
        ) : (
          <ul className={styles.list} aria-label="会话列表">
            {props.sessions.map((session) => (
              <li key={session.id}>{renderRow(session)}</li>
            ))}
          </ul>
        )}
        {props.onUnarchive && (
          <StudioChatArchivedSessions
            sessions={props.archivedSessions ?? []}
            pending={pending}
            run={run}
            onUnarchive={props.onUnarchive}
            onDelete={props.onDelete}
          />
        )}
        {error && (
          <div className={styles.error} role="alert">
            {error}
          </div>
        )}
      </Popover>
    </>
  )
}
