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
  onClose,
  onConfirm,
}: ConfirmDialogProps) {
  const [isSubmitting, setIsSubmitting] = useState(false)

  if (!open) return null

  const handleConfirm = async () => {
    setIsSubmitting(true)
    try {
      await onConfirm()
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
          {isSubmitting ? '删除中...' : confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
