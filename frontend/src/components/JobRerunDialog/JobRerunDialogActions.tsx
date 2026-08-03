import { Button, DialogActions } from '@mui/material'
import type { FailureCategoryState } from './useFailureCategories'

export type JobRerunDialogActionsProps = {
  failedMode: boolean
  failure: FailureCategoryState
  allowFailedNodeMode: boolean
  runnableCount: number
  itemLabel: string
  canConfirm: boolean
  loading: boolean
  onConfirm: () => void | Promise<void>
  onClose: () => void
}

/** 重跑对话框底部按钮行：取消 + 确认（文案随选择模式变化）。 */
export function JobRerunDialogActions({
  failedMode,
  failure,
  allowFailedNodeMode,
  runnableCount,
  itemLabel,
  canConfirm,
  loading,
  onConfirm,
  onClose,
}: JobRerunDialogActionsProps) {
  const confirmLabel = failedMode
    ? failure.confirmLabel
    : allowFailedNodeMode
      ? `重跑 ${runnableCount} 个${itemLabel}`
      : '确认重跑'
  return (
    <DialogActions>
      <Button variant="text" type="button" onClick={onClose} disabled={loading}>
        取消
      </Button>
      <Button
        variant="contained"
        onClick={onConfirm}
        disabled={!canConfirm || loading}
      >
        {confirmLabel}
      </Button>
    </DialogActions>
  )
}
