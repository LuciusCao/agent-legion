import { useMemo, useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import type { JobSummary, WorkflowDefinitionRecord } from '../../types'
import {
  computeOrderedNodes,
  type WorkflowNodesByKey,
} from '../../lib/workflowNodes'
import type { JobRerunConfirmArgs } from '../JobRerunDialog/useFailureCategories'
import { type FailureCategorySelection } from '../JobRerunDialog/failureCategoryCounts'
import { JobAllMatchingNodeRow } from './JobAllMatchingNodeRow'
import { JobAllMatchingFailureCategoryRow } from './JobAllMatchingFailureCategoryRow'
import styles from '../JobRerunDialog/JobRerunDialog.module.css'

export type JobAllMatchingRerunDialogProps = {
  open: boolean
  count: number
  jobs: JobSummary[]
  workflowDefinition?: WorkflowDefinitionRecord | null
  workflowNodesByKey?: WorkflowNodesByKey | null
  onClose: () => void
  onConfirm: (...args: JobRerunConfirmArgs) => void | Promise<void>
}

/**
 * Rerun dialog for 'allMatching' selections: from-node or failure-category
 * rerun, both resolved server-side from the selection filter.
 */
export function JobAllMatchingRerunDialog({
  open,
  count,
  jobs,
  workflowDefinition,
  workflowNodesByKey,
  onClose,
  onConfirm,
}: JobAllMatchingRerunDialogProps) {
  const [selection, setSelection] = useState<FailureCategorySelection>('all')
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const orderedNodes = useMemo(
    () => computeOrderedNodes(jobs, workflowDefinition, workflowNodesByKey),
    [jobs, workflowDefinition, workflowNodesByKey]
  )

  if (!open) return null

  const confirmArgs = (): JobRerunConfirmArgs =>
    selectedNodeKey
      ? [selectedNodeKey, false]
      : [null, true, undefined, selection === 'all' ? undefined : selection]

  const handleConfirm = async () => {
    setLoading(true)
    try {
      await onConfirm(...confirmArgs())
    } catch {
      // Keep the dialog open on failure; the store already surfaced a toast.
      return
    } finally {
      setLoading(false)
    }
    onClose()
  }

  const selectedNodeLabel = selectedNodeKey
    ? (orderedNodes.find((node) => node.key === selectedNodeKey)?.label ??
      selectedNodeKey)
    : null

  return (
    <Dialog open onClose={onClose}>
      <DialogTitle>批量重跑</DialogTitle>
      <DialogContent>
        <p>将对符合筛选条件的 {count} 个 job 执行（按筛选条件由服务端解析）</p>
        <JobAllMatchingNodeRow
          nodes={orderedNodes}
          selectedNodeKey={selectedNodeKey}
          onSelectNode={setSelectedNodeKey}
        />
        <JobAllMatchingFailureCategoryRow
          active={selectedNodeKey === null}
          selection={selection}
          onSelect={(value) => {
            setSelectedNodeKey(null)
            setSelection(value)
          }}
        />
        <div className={styles.summary}>
          {selectedNodeLabel
            ? `重跑节点：${selectedNodeLabel}（按筛选条件由服务端解析）`
            : '按失败类别重跑（按筛选条件由服务端解析）'}
        </div>
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
