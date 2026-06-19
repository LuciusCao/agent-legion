import { useMemo, useState } from 'react'
import {
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import type { JobSummary, WorkflowDefinitionRecord } from '../types'
import {
  computeOrderedNodes,
  excludedJobs,
  type WorkflowNodesByKey,
} from '../lib/workflowNodes'
import styles from './JobRerunDialog.module.css'

export type { WorkflowNodesByKey }

export type JobRerunDialogProps = {
  open: boolean
  jobs: JobSummary[]
  workflowDefinition?: WorkflowDefinitionRecord | null
  workflowNodesByKey?: WorkflowNodesByKey | null
  itemLabel?: string
  onConfirm: (nodeKey: string) => void | Promise<void>
  onClose: () => void
}

export function JobRerunDialog({
  open,
  jobs,
  workflowDefinition,
  workflowNodesByKey,
  itemLabel = '任务',
  onConfirm,
  onClose,
}: JobRerunDialogProps) {
  const orderedNodes = useMemo(
    () => computeOrderedNodes(jobs, workflowDefinition, workflowNodesByKey),
    [jobs, workflowDefinition, workflowNodesByKey]
  )
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(
    orderedNodes[0]?.key ?? null
  )

  // Keep selected key valid when nodes change
  const effectiveNodeKey =
    selectedNodeKey && orderedNodes.some((n) => n.key === selectedNodeKey)
      ? selectedNodeKey
      : (orderedNodes[0]?.key ?? null)

  const [loading, setLoading] = useState(false)
  const excluded = effectiveNodeKey
    ? excludedJobs(
        jobs,
        effectiveNodeKey,
        workflowNodesByKey,
        workflowDefinition
      )
    : []

  if (!open) return null

  const handleConfirm = async () => {
    if (!effectiveNodeKey) return
    setLoading(true)
    try {
      await onConfirm(effectiveNodeKey)
    } finally {
      setLoading(false)
    }
    onClose()
  }

  return (
    <Dialog
      open
      onClose={onClose}
      PaperProps={{
        sx: {
          minWidth: '520px',
          maxWidth: '760px',
          width: 'min(760px, 92vw)',
        },
      }}
    >
      <DialogTitle>选择重跑节点</DialogTitle>
      <DialogContent>
        <div className={styles.content}>
          {orderedNodes.length === 0 ? (
            <p className={styles.empty}>没有可重跑的公共节点</p>
          ) : (
            <div className={styles.nodeGrid}>
              {orderedNodes.map((node) => (
                <Chip
                  key={node.key}
                  data-testid={`rerun-chip-${node.key}`}
                  label={node.label || node.key}
                  variant={
                    effectiveNodeKey === node.key ? 'filled' : 'outlined'
                  }
                  onClick={() => setSelectedNodeKey(node.key)}
                />
              ))}
            </div>
          )}

          {excluded.length > 0 && effectiveNodeKey && (
            <div className={styles.excludedBox}>
              <div className={styles.excludedTitle}>
                以下任务不包含所选节点，将被跳过：
              </div>
              <ul className={styles.excludedList}>
                {excluded.map((job) => (
                  <li key={job.id}>{job.source_id || job.title || job.id}</li>
                ))}
              </ul>
            </div>
          )}

          <div className={styles.summary}>
            已选择 {jobs.length} 个{itemLabel}
            {effectiveNodeKey
              ? `，重跑节点：${orderedNodes.find((n) => n.key === effectiveNodeKey)?.label || effectiveNodeKey}`
              : ''}
          </div>
        </div>
      </DialogContent>
      <DialogActions>
        <Button
          variant="text"
          type="button"
          onClick={onClose}
          disabled={loading}
        >
          取消
        </Button>
        <Button
          variant="contained"
          onClick={handleConfirm}
          disabled={!effectiveNodeKey || loading}
        >
          确认重跑
        </Button>
      </DialogActions>
    </Dialog>
  )
}
