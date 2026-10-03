import { useState } from 'react'
import { IconButton, Popover, Tooltip } from '@mui/material'
import { DeleteOutline, EditOutlined, ExpandMore } from '@mui/icons-material'
import type { StudioChatSessionRecord } from './studioChatApi'
import { formatDateTime } from '../../../lib/formatters'
import styles from './StudioChatSessionMenu.module.css'

type Props = {
  sessions: StudioChatSessionRecord[]
  activeSessionId: string | null
  onSelectSession: (sessionId: string) => void
  /** 会话管理动作（#872）；不传则菜单只做选择。失败经 reject 回传，行内展示。 */
  onRename?: (sessionId: string, title: string) => Promise<void>
  onDelete?: (sessionId: string) => Promise<void>
}

export function sessionLabel(session: StudioChatSessionRecord): string {
  if (session.title) return session.title
  return `对话 ${formatDateTime(session.created_at)}`
}

type Mode = { kind: 'rename' | 'delete'; sessionId: string } | null

/** 会话选择器（#872）：原生 select 换成按钮 + 弹出列表，列表项右侧挂
 * 改名（就地编辑：Enter 保存 / Esc 取消）与删除（行内二次确认）入口。 */
export function StudioChatSessionMenu(props: Props) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const [mode, setMode] = useState<Mode>(null)
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const active = props.sessions.find((row) => row.id === props.activeSessionId)

  function close() {
    setAnchor(null)
    setMode(null)
    setError(null)
  }

  async function run(action: () => Promise<void>) {
    setPending(true)
    setError(null)
    try {
      await action()
      setMode(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败')
    } finally {
      setPending(false)
    }
  }

  function renderRow(session: StudioChatSessionRecord) {
    const label = sessionLabel(session)
    const closed = session.status === 'closed' || session.status === 'error'
    if (mode?.sessionId === session.id && mode.kind === 'rename') {
      const save = () =>
        void run(() => props.onRename!(session.id, draft.trim()))
      return (
        <div className={styles.rowEdit}>
          <input
            className={styles.titleInput}
            aria-label="会话名称"
            value={draft}
            maxLength={200}
            autoFocus
            disabled={pending}
            placeholder={`对话 ${formatDateTime(session.created_at)}`}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              // Esc 只退出编辑：不冒泡到 Popover（否则整个菜单一起关）。
              if (event.key === 'Escape') {
                event.preventDefault()
                event.stopPropagation()
                setMode(null)
              } else if (
                event.key === 'Enter' &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault()
                save()
              }
            }}
          />
          <button
            type="button"
            className={styles.textAction}
            disabled={pending}
            onClick={save}
          >
            保存
          </button>
          <button
            type="button"
            className={styles.textAction}
            disabled={pending}
            onClick={() => setMode(null)}
          >
            取消
          </button>
        </div>
      )
    }
    if (mode?.sessionId === session.id && mode.kind === 'delete') {
      return (
        <div className={styles.rowConfirm} role="alert">
          <span className={styles.confirmText}>
            删除「{label}」？
            {!closed && '会先关闭运行中的会话，'}删除后不可恢复
          </span>
          <button
            type="button"
            className={styles.dangerAction}
            disabled={pending}
            onClick={() => void run(() => props.onDelete!(session.id))}
          >
            删除
          </button>
          <button
            type="button"
            className={styles.textAction}
            disabled={pending}
            onClick={() => setMode(null)}
          >
            取消
          </button>
        </div>
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
                setDraft(session.title)
                setError(null)
                setMode({ kind: 'rename', sessionId: session.id })
              }}
            >
              <EditOutlined fontSize="inherit" />
            </IconButton>
          </Tooltip>
        )}
        {props.onDelete && (
          <Tooltip title="删除">
            <IconButton
              size="small"
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
        {error && (
          <div className={styles.error} role="alert">
            {error}
          </div>
        )}
      </Popover>
    </>
  )
}
