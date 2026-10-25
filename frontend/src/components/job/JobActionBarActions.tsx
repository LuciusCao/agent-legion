import { useState } from 'react'
import { Button } from '@mui/material'
import type { JobActionBarProps } from './JobActionBar'
import { JobRerunDialog } from '../JobRerunDialog'
import { JobRunToDialog } from './JobRunToDialog'
import { JobAllMatchingRerunDialog } from './JobAllMatchingRerunDialog'
import { JobAllMatchingUpgradeDialog } from './JobAllMatchingUpgradeDialog'
import {
  JobActionBarBatchButtons,
  JobRunToAction,
} from './JobActionBarBatchButtons'
import { JobBatchApprovalButtons } from './JobApprovalActionButton'
import { ConfirmDialog } from '../ConfirmDialog'
import type { ApprovalBatchKind } from '../../api/jobApprovalApi'
import {
  allMatchingActionDisabled,
  canApproveJob,
  canContinueJob,
  computeActionDisabled,
} from '../jobActionEligibility'
import styles from './JobActionBar.module.css'

type JobActionBarActionsProps = JobActionBarProps & { isBatch: boolean }

export function JobActionBarActions(props: JobActionBarActionsProps) {
  const {
    jobs,
    workspaceId,
    workflowDefinition,
    workflowNodesByKey,
    isBatch,
    loading = false,
    failureContext,
    allMatchingCount = null,
    onRerun,
    onRunTo,
    onContinue,
    onPackage,
    onClearPacked,
    onDelete,
    onPause,
    onResume,
    onApproval,
    onUpgradeWorkflow,
    onExitSelectMode,
    itemLabel = '任务',
  } = props
  const [rerunOpen, setRerunOpen] = useState(false)
  const [runToOpen, setRunToOpen] = useState(false)
  const [upgradeOpen, setUpgradeOpen] = useState(false)
  const [approval, setApproval] = useState<ApprovalBatchKind | null>(null)
  const allMatching = allMatchingCount != null
  const disabled = allMatching
    ? allMatchingActionDisabled(allMatchingCount, loading)
    : computeActionDisabled(jobs, loading)
  const approvalEligible = jobs.filter(canApproveJob).length
  const selectedCount = props.selectedCount ?? jobs.length
  const handleRunTo = (targetKey: string, startKey?: string) =>
    onRunTo?.(targetKey, startKey)

  return (
    <>
      <div className={styles.actions}>
        {isBatch && onApproval && (
          <JobBatchApprovalButtons
            disabled={loading || allMatching || approvalEligible === 0}
            onOpenApproval={setApproval}
          />
        )}
        {isBatch && (
          <JobActionBarBatchButtons
            allMatching={allMatching}
            allMatchingCount={allMatchingCount ?? 0}
            jobs={jobs}
            itemLabel={itemLabel}
            loading={loading}
            onOpenUpgrade={() => setUpgradeOpen(true)}
            onUpgradeWorkflow={onUpgradeWorkflow}
            onPause={onPause}
            onResume={onResume}
          />
        )}
        <Button
          variant="outlined"
          onClick={() => setRerunOpen(true)}
          disabled={disabled.rerun}
        >
          重跑
        </Button>
        <JobRunToAction
          allMatching={allMatching}
          disabled={disabled.runTo}
          onOpen={() => setRunToOpen(true)}
        />
        {!isBatch && jobs.some((job) => canContinueJob(job)) && (
          <Button
            variant="outlined"
            onClick={onContinue}
            disabled={disabled.continue}
          >
            继续完整流程
          </Button>
        )}
        <Button
          variant="outlined"
          onClick={onPackage}
          disabled={disabled.package}
        >
          打包
        </Button>
        {onClearPacked && (
          <Button
            variant="outlined"
            onClick={onClearPacked}
            disabled={disabled.clearPacked}
          >
            清空打包状态
          </Button>
        )}
        <Button
          variant="outlined"
          color="error"
          onClick={onDelete}
          disabled={disabled.delete}
        >
          删除
        </Button>
        {isBatch && onExitSelectMode && (
          <Button variant="outlined" onClick={onExitSelectMode}>
            退出
          </Button>
        )}
      </div>

      {onApproval && (
        <ConfirmDialog
          open={approval !== null}
          title={approval === 'approve' ? '确认批量通过' : '确认批量终止'}
          confirmLabel={approval === 'approve' ? '确认通过' : '确认终止'}
          confirmColor={approval === 'reject' ? 'error' : 'primary'}
          busyLabel="提交中..."
          onClose={() => setApproval(null)}
          onConfirm={async () => {
            if (!approval) return
            await onApproval(approval)
            setApproval(null)
          }}
        >
          <p>
            已选择 {selectedCount} 项，预计执行 {approvalEligible} 项、跳过{' '}
            {Math.max(0, selectedCount - approvalEligible)} 项。
            <br />
            {approval === 'approve' ? '通过' : '终止'}
            当前待审批门；实际结果以提交结果或任务中心为准。
          </p>
        </ConfirmDialog>
      )}

      {allMatching ? (
        <JobAllMatchingRerunDialog
          open={rerunOpen}
          count={allMatchingCount}
          jobs={jobs}
          workspaceId={workspaceId}
          workflowDefinition={workflowDefinition}
          workflowNodesByKey={workflowNodesByKey}
          onClose={() => setRerunOpen(false)}
          onConfirm={onRerun}
        />
      ) : (
        <JobRerunDialog
          open={rerunOpen}
          jobs={jobs}
          workflowDefinition={workflowDefinition}
          workflowNodesByKey={workflowNodesByKey}
          itemLabel={itemLabel}
          allowFailedNodeMode={isBatch}
          failureContext={failureContext}
          onClose={() => setRerunOpen(false)}
          onConfirm={onRerun}
        />
      )}
      <JobRunToDialog
        open={runToOpen}
        jobs={jobs}
        workflowDefinition={workflowDefinition}
        workflowNodesByKey={workflowNodesByKey}
        itemLabel={itemLabel}
        onClose={() => setRunToOpen(false)}
        onConfirm={handleRunTo}
      />
      {allMatching && (
        <JobAllMatchingUpgradeDialog
          open={upgradeOpen}
          count={allMatchingCount ?? 0}
          onClose={() => setUpgradeOpen(false)}
          onConfirm={(mode) => onUpgradeWorkflow?.(undefined, mode)}
        />
      )}
    </>
  )
}
