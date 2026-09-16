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

interface JobWorkflowUpgradeDialogProps {
  open: boolean
  onClose: () => void
  onConfirm: (mode: UpgradeMode) => void | Promise<void>
}

/** 单 job 升级确认对话框（issue #645）：选择 clean / inherit 后升级。 */
export function JobWorkflowUpgradeDialog({
  open,
  onClose,
  onConfirm,
}: JobWorkflowUpgradeDialogProps) {
  const [mode, setMode] = useState<UpgradeMode>('clean')
  const [isUpgrading, setIsUpgrading] = useState(false)

  if (!open) return null

  const handleConfirm = async () => {
    setIsUpgrading(true)
    try {
      await onConfirm(mode)
      onClose()
    } finally {
      setIsUpgrading(false)
    }
  }

  return (
    <Dialog open={open} onClose={onClose}>
      <DialogTitle>升级 workflow</DialogTitle>
      <DialogContent>
        <UpgradeModeSelector value={mode} onChange={setMode} />
        <p>
          {mode === 'clean'
            ? '升级后将重置全部节点并清空产物。'
            : '未变节点将继承既有产物，仅重跑变化的子图。'}
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
