import { useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'

interface JobAllMatchingUpgradeDialogProps {
  open: boolean
  count: number
  onClose: () => void
  onConfirm: () => void | Promise<void>
}

export function JobAllMatchingUpgradeDialog({
  open,
  count,
  onClose,
  onConfirm,
}: JobAllMatchingUpgradeDialogProps) {
  const [isUpgrading, setIsUpgrading] = useState(false)

  if (!open) return null

  const handleConfirm = async () => {
    setIsUpgrading(true)
    try {
      await onConfirm()
      onClose()
    } finally {
      setIsUpgrading(false)
    }
  }

  return (
    <Dialog open={open} onClose={onClose}>
      <DialogTitle>确认升级 workflow</DialogTitle>
      <DialogContent>
        <p>
          将对符合筛选条件的 {count} 个 job 执行 workflow
          升级。已是最新版本或运行中的 job 会自动跳过。
        </p>
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={onClose} disabled={isUpgrading}>
          取消
        </Button>
        <Button
          variant="contained"
          onClick={handleConfirm}
          disabled={isUpgrading}
        >
          {isUpgrading ? '升级中...' : '确认升级'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
