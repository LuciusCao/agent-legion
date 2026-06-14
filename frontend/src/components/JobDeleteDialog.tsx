import { useCallback, useState } from 'react'

interface JobDeleteDialogProps {
  open: boolean
  title?: string
  onClose: () => void
  onConfirm: () => void | Promise<void>
}

export function JobDeleteDialog({
  open,
  title,
  onClose,
  onConfirm,
}: JobDeleteDialogProps) {
  const [isDeleting, setIsDeleting] = useState(false)

  const handleConfirm = useCallback(async () => {
    setIsDeleting(true)
    try {
      await onConfirm()
    } finally {
      setIsDeleting(false)
    }
  }, [onConfirm])

  if (!open) return null

  return (
    <md-dialog
      open
      onClosed={onClose}
      style={
        { '--md-dialog-container-color': '#ffffff' } as React.CSSProperties
      }
    >
      <div slot="headline">确认删除</div>
      <div slot="content">
        <p>
          确定删除任务 {title ? <strong>{title}</strong> : '此任务'}{' '}
          吗？删除后不可恢复。
        </p>
      </div>
      <div slot="actions">
        <md-text-button onClick={onClose} disabled={isDeleting || undefined}>
          取消
        </md-text-button>
        <md-filled-button
          style={
            {
              '--md-sys-color-primary': 'var(--md-sys-color-error)',
            } as React.CSSProperties
          }
          onClick={handleConfirm}
          disabled={isDeleting || undefined}
        >
          {isDeleting ? '删除中...' : '删除'}
        </md-filled-button>
      </div>
    </md-dialog>
  )
}
