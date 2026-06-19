import { useCallback, useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
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

  return (
    <Dialog open={deleteDialogOpen} onClose={closeDeleteDialog}>
      <DialogTitle>确认删除</DialogTitle>
      <DialogContent>
        <p>确定删除该资源？本地视频和处理产物目录也会删除。</p>
      </DialogContent>
      <DialogActions>
        <Button
          variant="text"
          onClick={closeDeleteDialog}
          disabled={isDeleting}
        >
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
