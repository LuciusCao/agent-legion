import { useCallback, useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'

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

  return (
    <Dialog open={open} onClose={onClose}>
      <DialogTitle>确认删除</DialogTitle>
      <DialogContent>
        <p>
          确定删除任务 {title ? <strong>{title}</strong> : '此任务'}{' '}
          吗？删除后不可恢复。
        </p>
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={onClose} disabled={isDeleting}>
          取消
        </Button>
        <Button
          variant="contained"
          color="error"
          onClick={handleConfirm}
          disabled={isDeleting}
        >
          {isDeleting ? '删除中...' : '删除'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
