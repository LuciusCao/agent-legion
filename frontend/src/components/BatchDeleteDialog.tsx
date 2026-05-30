import { useState } from 'react'

interface BatchDeleteDialogProps {
  open: boolean
  count: number
  onClose: () => void
  onConfirm: () => Promise<void>
}

export function BatchDeleteDialog({
  open,
  count,
  onClose,
  onConfirm,
}: BatchDeleteDialogProps) {
  const [isDeleting, setIsDeleting] = useState(false)

  if (!open) return null

  const handleConfirm = async () => {
    setIsDeleting(true)
    try {
      await onConfirm()
    } finally {
      setIsDeleting(false)
    }
  }

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
        <p>确定删除 {count} 个资源？本地视频和处理产物目录也会删除。</p>
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
