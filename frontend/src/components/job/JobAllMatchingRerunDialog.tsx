import { useMemo, useState } from 'react'
import { Dialog, DialogContent, DialogTitle } from '@mui/material'
import type { JobSummary, WorkflowDefinitionRecord } from '../../types'
import {
  computeOrderedNodes,
  type WorkflowNodesByKey,
} from '../../lib/workflowNodes'
import type { JobRerunConfirmArgs } from '../JobRerunDialog/useFailureCategories'
import { type FailureCategorySelection } from '../JobRerunDialog/failureCategoryCounts'
import { JobAllMatchingNodeRow } from './JobAllMatchingNodeRow'
import { JobAllMatchingFailureCategoryRow } from './JobAllMatchingFailureCategoryRow'
import { JobAllMatchingRerunFooter } from './JobAllMatchingRerunFooter'
import { useBatchRerunPreview } from './useBatchRerunPreview'

export type JobAllMatchingRerunDialogProps = {
  open: boolean
  count: number
  jobs: JobSummary[]
  workspaceId?: string
  workflowDefinition?: WorkflowDefinitionRecord | null
  workflowNodesByKey?: WorkflowNodesByKey | null
  onClose: () => void
  onConfirm: (...args: JobRerunConfirmArgs) => void | Promise<void>
}

/**
 * Rerun dialog for 'allMatching' selections: from-node or failure-category
 * rerun, both resolved server-side from the selection filter. The footer
 * shows the server-computed 「将重跑 N 个任务」 once the preview answers.
 */
export function JobAllMatchingRerunDialog({
  open,
  count,
  jobs,
  workspaceId,
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

  const preview = useBatchRerunPreview(
    workspaceId,
    open,
    selectedNodeKey
      ? { kind: 'node', nodeKey: selectedNodeKey }
      : selection === 'all'
        ? { kind: 'failedNode' }
        : { kind: 'category', category: selection }
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
      </DialogContent>
      <JobAllMatchingRerunFooter
        eligibleCount={preview.data?.eligible_count}
        selectedNodeLabel={
          selectedNodeKey
            ? (orderedNodes.find((node) => node.key === selectedNodeKey)
                ?.label ?? selectedNodeKey)
            : null
        }
        loading={loading}
        count={count}
        onClose={onClose}
        onConfirm={handleConfirm}
      />
    </Dialog>
  )
}
