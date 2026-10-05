import { useState } from 'react'
import type { ReactNode } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'

interface ConfirmDialogProps {
  open: boolean
  title: string
  children: ReactNode
  confirmLabel?: string
  /** 提交在途时确认按钮的文案（默认「删除中...」）。 */
  busyLabel?: string
  onClose: () => void
  onConfirm: () => Promise<void>
}

/**
 * Shared destructive-action confirm dialog (MUI), replacing native
 * window.confirm; mirrors BatchDeleteDialog's busy-state handling.
 */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel = '删除',
  busyLabel = '删除中...',
  onClose,
  onConfirm,
}: ConfirmDialogProps) {
  const [isSubmitting, setIsSubmitting] = useState(false)

  if (!open) return null

  const handleConfirm = async () => {
    setIsSubmitting(true)
    try {
      await onConfirm()
    } catch (err) {
      // onConfirm 负责把自己的错误呈现给用户（两个调用方都 setError 后
      // 关闭弹窗）；这里只兜底记录，避免 unhandled rejection 静默丢失。
      console.error('ConfirmDialog onConfirm failed:', err)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onClose={onClose}>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>{children}</DialogContent>
      <DialogActions>
        <Button variant="text" onClick={onClose} disabled={isSubmitting}>
          取消
        </Button>
        <Button
          variant="contained"
          color="error"
          onClick={() => void handleConfirm()}
          disabled={isSubmitting}
        >
          {isSubmitting ? busyLabel : confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
