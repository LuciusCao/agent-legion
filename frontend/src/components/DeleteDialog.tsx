import { useCallback, useState } from 'react'
import { useUiStore } from '../stores/uiStore'

interface DeleteDialogProps {
  onConfirm: () => Promise<boolean>
}

export function DeleteDialog({ onConfirm }: DeleteDialogProps) {
  const { deleteDialogOpen, closeDeleteDialog } = useUiStore()
  const [isDeleting, setIsDeleting] = useState(false)

  const handleConfirm = useCallback(async () => {
    setIsDeleting(true)
    try {
      const shouldClose = await onConfirm()
      if (shouldClose) {
        closeDeleteDialog()
      }
    } finally {
      setIsDeleting(false)
    }
  }, [onConfirm, closeDeleteDialog])

  if (!deleteDialogOpen) return null

  return (
    <md-dialog
      open
      onClosed={closeDeleteDialog}
      style={
        { '--md-dialog-container-color': '#ffffff' } as React.CSSProperties
      }
    >
      <div slot="headline">确认删除</div>
      <div slot="content">
        <p>确定删除该资源？本地视频和处理产物目录也会删除。</p>
      </div>
      <div slot="actions">
        <md-text-button
          onClick={closeDeleteDialog}
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
          disabled={isDeleting || undefined}
        >
          {isDeleting ? '删除中...' : '删除'}
        </md-filled-button>
      </div>
    </md-dialog>
  )
}
