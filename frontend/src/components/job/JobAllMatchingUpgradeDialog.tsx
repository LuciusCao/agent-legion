import { useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import type { UpgradeMode } from '../../types/jobTypes'
import { UpgradeModeSelector } from './UpgradeModeSelector'

interface JobAllMatchingUpgradeDialogProps {
  open: boolean
  count: number
  onClose: () => void
  onConfirm: (mode: UpgradeMode) => void | Promise<void>
}

export function JobAllMatchingUpgradeDialog({
  open,
  count,
  onClose,
  onConfirm,
}: JobAllMatchingUpgradeDialogProps) {
  const [mode, setMode] = useState<UpgradeMode>('clean')
  const [isUpgrading, setIsUpgrading] = useState(false)

  if (!open) return null

  const handleConfirm = async () => {
    setIsUpgrading(true)
    try {
      await onConfirm(mode)
      onClose()
    } catch {
      // The action owns error presentation. Keep the dialog (and selected
      // mode) open so the user can retry after a failed request.
    } finally {
      setIsUpgrading(false)
    }
  }

  return (
    <Dialog open={open} onClose={onClose}>
      <DialogTitle>确认升级 workflow</DialogTitle>
      <DialogContent>
        <UpgradeModeSelector value={mode} onChange={setMode} />
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
