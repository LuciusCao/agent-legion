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
import { normalizeJobStatus } from '../stores/job/state'
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
  allowFailedNodeMode?: boolean
  onConfirm: (nodeKey: string | null, fromFailedNode: boolean) => void | Promise<void>
  onClose: () => void
}

export function JobRerunDialog({
  open,
  jobs,
  workflowDefinition,
  workflowNodesByKey,
  itemLabel = '任务',
  allowFailedNodeMode = false,
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
  const [failedMode, setFailedMode] = useState(false)
  const [loading, setLoading] = useState(false)

  // Keep selected node valid when orderedNodes changes
  const effectiveNodeKey = useMemo(() => {
    if (failedMode) return null
    if (selectedNodeKey && orderedNodes.some((n) => n.key === selectedNodeKey)) {
      return selectedNodeKey
    }
    return orderedNodes[0]?.key ?? null
  }, [failedMode, orderedNodes, selectedNodeKey])

  const failedJobs = useMemo(
    () => jobs.filter((j) => normalizeJobStatus(j.status) === 'failed'),
    [jobs]
  )
  const nonFailedJobs = useMemo(
    () => jobs.filter((j) => normalizeJobStatus(j.status) !== 'failed'),
    [jobs]
  )

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
    if (failedMode) {
      setLoading(true)
      try {
        await onConfirm(null, true)
      } finally {
        setLoading(false)
      }
      onClose()
      return
    }
    if (!effectiveNodeKey) return
    setLoading(true)
    try {
      await onConfirm(effectiveNodeKey, false)
    } finally {
      setLoading(false)
    }
    onClose()
  }

  const canConfirm = failedMode ? failedJobs.length > 0 : !!effectiveNodeKey

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
          {orderedNodes.length === 0 && (!allowFailedNodeMode || failedJobs.length === 0) ? (
            <p className={styles.empty}>没有可重跑的节点</p>
          ) : (
            <div className={styles.nodeGrid}>
              {allowFailedNodeMode && (
                <Chip
                  data-testid="rerun-chip-failed-node"
                  label="失败的节点"
                  color="error"
                  variant={failedMode ? 'filled' : 'outlined'}
                  onClick={() => setFailedMode(true)}
                />
              )}
              {orderedNodes.map((node) => (
                <Chip
                  key={node.key}
                  data-testid={`rerun-chip-${node.key}`}
                  label={node.label || node.key}
                  variant={
                    !failedMode && effectiveNodeKey === node.key
                      ? 'filled'
                      : 'outlined'
                  }
                  onClick={() => {
                    setFailedMode(false)
                    setSelectedNodeKey(node.key)
                  }}
                />
              ))}
            </div>
          )}

          {failedMode && nonFailedJobs.length > 0 && (
            <div className={styles.excludedBox}>
              <div className={styles.excludedTitle}>
                以下任务未失败，将被跳过：
              </div>
              <ul className={styles.excludedList}>
                {nonFailedJobs.map((job) => (
                  <li key={job.id}>{job.source_id || job.title || job.id}</li>
                ))}
              </ul>
            </div>
          )}

          {!failedMode && excluded.length > 0 && effectiveNodeKey && (
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
            {failedMode
              ? `已选择 ${jobs.length} 个${itemLabel}，其中 ${failedJobs.length} 个失败任务将从各自失败节点重跑`
              : `已选择 ${jobs.length} 个${itemLabel}${effectiveNodeKey ? `，重跑节点：${orderedNodes.find((n) => n.key === effectiveNodeKey)?.label || effectiveNodeKey}` : ''}`}
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
          disabled={!canConfirm || loading}
        >
          {failedMode
            ? `重跑 ${failedJobs.length} 个失败任务`
            : '确认重跑'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
