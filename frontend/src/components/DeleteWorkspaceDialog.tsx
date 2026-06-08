import { useState } from 'react'

type Props = {
  open: boolean
  workspaceName: string
  workspaceId: string
  onClose: () => void
  onConfirm: () => Promise<void>
}

export default function DeleteWorkspaceDialog({
  open,
  workspaceName,
  onClose,
  onConfirm,
}: Props) {
  const [inputValue, setInputValue] = useState('')
  const [isDeleting, setIsDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function handleClose() {
    setInputValue('')
    setError(null)
    setIsDeleting(false)
    onClose()
  }

  if (!open) return null

  const confirmed = inputValue.trim() === workspaceName.trim()

  async function handleConfirm() {
    if (!confirmed) return
    setError(null)
    setIsDeleting(true)
    try {
      await onConfirm()
      handleClose()
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : '删除失败，请稍后重试'
      setError(message)
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <md-dialog
      open
      onClosed={handleClose}
      style={
        { '--md-dialog-container-color': '#ffffff' } as React.CSSProperties
      }
    >
      <div slot="headline">删除 Workspace</div>
      <div
        slot="content"
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
          minWidth: 320,
        }}
      >
        <div
          style={{
            padding: 12,
            borderRadius: 8,
            background: 'var(--md-sys-color-error-container)',
            color: 'var(--md-sys-color-on-error-container)',
            fontSize: 14,
            lineHeight: 1.6,
          }}
        >
          <strong>此操作不可撤销。</strong>
          <br />
          删除后，Workspace「{workspaceName}」及其所有任务记录将被永久移除，
          但磁盘上的产物文件不会自动清理。
        </div>
        <p
          style={{
            margin: 0,
            fontSize: 14,
            color: 'var(--md-sys-color-on-surface-variant)',
          }}
        >
          请输入 Workspace 名称「<strong>{workspaceName}</strong>」以确认删除。
        </p>
        <md-outlined-text-field
          label="Workspace 名称"
          aria-label="Workspace 名称"
          value={inputValue}
          onInput={(e: Event) =>
            setInputValue((e.target as HTMLInputElement).value)
          }
          disabled={isDeleting || undefined}
          style={{ width: '100%' }}
        />
        {error && (
          <div style={{ color: 'var(--md-sys-color-error)', fontSize: 13 }}>
            {error}
          </div>
        )}
      </div>
      <div slot="actions">
        <md-text-button
          onClick={handleClose}
          disabled={isDeleting || undefined}
        >
          取消
        </md-text-button>
        <md-filled-button
          style={
            {
              '--md-sys-color-primary': 'var(--md-sys-color-error)',
            } as React.CSSProperties
          }
          onClick={handleConfirm}
          disabled={!confirmed || isDeleting || undefined}
        >
          {isDeleting ? '删除中…' : '确认删除'}
        </md-filled-button>
      </div>
    </md-dialog>
  )
}
