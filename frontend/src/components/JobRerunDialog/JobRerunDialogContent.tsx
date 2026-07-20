import {
  Button,
  Chip,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import type { JobSummary, WorkflowDefinitionRecord } from '../../types'
import {
  computeOrderedNodes,
  type WorkflowNodesByKey,
} from '../../lib/workflowNodes'
import { JobRerunExcludedLists } from './JobRerunExcludedLists'
import { JobRerunSelectionSummary } from './JobRerunSelectionSummary'
import styles from './JobRerunDialog.module.css'

export type JobRerunDialogContentProps = {
  jobs: JobSummary[]
  workflowDefinition?: WorkflowDefinitionRecord | null
  workflowNodesByKey?: WorkflowNodesByKey | null
  itemLabel?: string
  allowFailedNodeMode?: boolean
  failedMode: boolean
  setFailedMode: (value: boolean) => void
  effectiveNodeKey: string | null
  setSelectedNodeKey: (key: string) => void
  failedJobs: JobSummary[]
  nonFailedJobs: JobSummary[]
  excluded: JobSummary[]
  runnableJobs: JobSummary[]
  notStartedJobs: JobSummary[]
  runningJobs: JobSummary[]
  canConfirm: boolean
  loading: boolean
  onConfirm: () => void | Promise<void>
  onClose: () => void
}

export function JobRerunDialogContent({
  jobs,
  workflowDefinition,
  workflowNodesByKey,
  itemLabel = '任务',
  allowFailedNodeMode = false,
  failedMode,
  setFailedMode,
  effectiveNodeKey,
  setSelectedNodeKey,
  failedJobs,
  nonFailedJobs,
  excluded,
  runnableJobs,
  notStartedJobs,
  runningJobs,
  canConfirm,
  loading,
  onConfirm,
  onClose,
}: JobRerunDialogContentProps) {
  const orderedNodes = computeOrderedNodes(
    jobs,
    workflowDefinition,
    workflowNodesByKey
  )

  return (
    <>
      <DialogTitle>选择重跑节点</DialogTitle>
      <DialogContent>
        <div className={styles.content}>
          {orderedNodes.length === 0 &&
          (!allowFailedNodeMode || failedJobs.length === 0) ? (
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

          <JobRerunExcludedLists
            failedMode={failedMode}
            effectiveNodeKey={effectiveNodeKey}
            nonFailedJobs={nonFailedJobs}
            excluded={excluded}
          />

          {!failedMode && allowFailedNodeMode ? (
            <JobRerunSelectionSummary
              jobs={jobs}
              itemLabel={itemLabel}
              runnableJobs={runnableJobs}
              notStartedJobs={notStartedJobs}
              runningJobs={runningJobs}
            />
          ) : (
            <div className={styles.summary}>
              {failedMode
                ? `已选择 ${jobs.length} 个${itemLabel}，其中 ${failedJobs.length} 个失败任务将从各自失败节点重跑`
                : `已选择 ${jobs.length} 个${itemLabel}${effectiveNodeKey ? `，重跑节点：${orderedNodes.find((n) => n.key === effectiveNodeKey)?.label || effectiveNodeKey}` : ''}`}
            </div>
          )}
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
          onClick={onConfirm}
          disabled={!canConfirm || loading}
        >
          {failedMode
            ? `重跑 ${failedJobs.length} 个失败任务`
            : allowFailedNodeMode
              ? `重跑 ${runnableJobs.length} 个${itemLabel}`
              : '确认重跑'}
        </Button>
      </DialogActions>
    </>
  )
}
