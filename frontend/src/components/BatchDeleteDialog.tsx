import { useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'

interface BatchDeleteDialogProps {
  open: boolean
  count: number
  allMatching?: boolean
  onClose: () => void
  onConfirm: () => Promise<void>
}

export function BatchDeleteDialog({
  open,
  count,
  allMatching = false,
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
    <Dialog open={open} onClose={onClose}>
      <DialogTitle>确认删除</DialogTitle>
      <DialogContent>
        {allMatching ? (
          <p>
            将对符合筛选条件的 {count} 个 job
            执行删除。本地视频和处理产物目录也会删除。
          </p>
        ) : (
          <p>确定删除 {count} 个资源？本地视频和处理产物目录也会删除。</p>
        )}
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
