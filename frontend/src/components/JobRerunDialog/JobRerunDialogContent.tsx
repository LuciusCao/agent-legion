import { Chip, DialogContent, DialogTitle } from '@mui/material'
import type { JobSummary } from '../../types'
import type { NodeCatalog } from '../../lib/nodeCatalog'
import {
  computeOrderedNodes,
  type WorkflowNodesByKey,
} from '../../lib/workflowNodes'
import { JobRerunExcludedLists } from './JobRerunExcludedLists'
import { JobRerunSelectionSummary } from './JobRerunSelectionSummary'
import { JobRerunFailureCategoryRow } from './JobRerunFailureCategoryRow'
import { JobRerunFromNodeRow } from './JobRerunFromNodeRow'
import { JobRerunDialogActions } from './JobRerunDialogActions'
import { failedModeSummaryText } from './failureCategoryCounts'
import type { FailureCategoryState } from './useFailureCategories'
import styles from './JobRerunDialog.module.css'

export type JobRerunDialogContentProps = {
  jobs: JobSummary[]
  workflowDefinition?: NodeCatalog | null
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
  failure: FailureCategoryState
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
  failure,
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

          {failedMode && <JobRerunFailureCategoryRow failure={failure} />}
          {failedMode && orderedNodes.length > 0 && (
            <JobRerunFromNodeRow nodes={orderedNodes} failure={failure} />
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
                ? failedModeSummaryText(failure, jobs.length, itemLabel)
                : `已选择 ${jobs.length} 个${itemLabel}${effectiveNodeKey ? `，重跑节点：${orderedNodes.find((n) => n.key === effectiveNodeKey)?.label || effectiveNodeKey}` : ''}`}
            </div>
          )}
        </div>
      </DialogContent>
      <JobRerunDialogActions
        failedMode={failedMode}
        failure={failure}
        allowFailedNodeMode={allowFailedNodeMode}
        runnableCount={runnableJobs.length}
        itemLabel={itemLabel}
        canConfirm={canConfirm}
        loading={loading}
        onConfirm={onConfirm}
        onClose={onClose}
      />
    </>
  )
}
