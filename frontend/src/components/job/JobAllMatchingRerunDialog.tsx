import { useState } from 'react'
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import type { FailureCategory } from '../../types/failureTypes'
import {
  FAILURE_CATEGORY_HINTS,
  FAILURE_CATEGORY_LABELS,
  FAILURE_CATEGORY_ORDER,
  type FailureCategorySelection,
} from '../JobRerunDialog/failureCategoryCounts'

export type JobAllMatchingRerunDialogProps = {
  open: boolean
  count: number
  onClose: () => void
  onConfirm: (
    nodeKey: string | null,
    fromFailedNode?: boolean,
    jobIds?: string[],
    failureCategory?: FailureCategory
  ) => void | Promise<void>
}

/**
 * Rerun dialog for filter-based ('allMatching') selections. Node-specific
 * rerun needs per-job client-side eligibility and is unavailable here, so
 * only the failure-category flow is offered; the payload is resolved
 * server-side from the selection filter.
 */
export function JobAllMatchingRerunDialog({
  open,
  count,
  onClose,
  onConfirm,
}: JobAllMatchingRerunDialogProps) {
  const [selection, setSelection] = useState<FailureCategorySelection>('all')
  const [loading, setLoading] = useState(false)

  if (!open) return null

  const handleConfirm = async () => {
    setLoading(true)
    try {
      await onConfirm(
        null,
        true,
        undefined,
        selection === 'all' ? undefined : selection
      )
    } catch {
      // Keep the dialog open on failure; the store already surfaced a toast.
      return
    } finally {
      setLoading(false)
    }
    onClose()
  }

  return (
    <Dialog open onClose={onClose}>
      <DialogTitle>按失败类别重跑</DialogTitle>
      <DialogContent>
        <p>将对符合筛选条件的 {count} 个 job 执行</p>
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
          <Chip
            data-testid="rerun-chip-all-failed"
            label="全部失败"
            color="error"
            variant={selection === 'all' ? 'filled' : 'outlined'}
            onClick={() => setSelection('all')}
          />
          {FAILURE_CATEGORY_ORDER.map((category) => (
            <Chip
              key={category}
              data-testid={`rerun-chip-${category}`}
              label={FAILURE_CATEGORY_LABELS[category]}
              title={FAILURE_CATEGORY_HINTS[category]}
              variant={selection === category ? 'filled' : 'outlined'}
              onClick={() => setSelection(category)}
            />
          ))}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={onClose} disabled={loading}>
          取消
        </Button>
        <Button
          variant="contained"
          onClick={handleConfirm}
          disabled={loading || count === 0}
        >
          确认重跑
        </Button>
      </DialogActions>
    </Dialog>
  )
}
