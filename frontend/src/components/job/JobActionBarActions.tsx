import { useState } from 'react'
import { Button, Tooltip } from '@mui/material'
import type { JobActionBarProps } from './JobActionBar'
import { JobRerunDialog } from '../JobRerunDialog'
import { JobRunToDialog } from './JobRunToDialog'
import { JobActionBarUpgrade } from './JobActionBarUpgrade'
import { JobAllMatchingRerunDialog } from './JobAllMatchingRerunDialog'
import {
  canContinueJob,
  computeActionDisabled,
  type JobActionDisabled,
} from '../jobActionEligibility'
import styles from './JobActionBar.module.css'

const ALL_MATCHING_TOOLTIP = '全量选择模式下不可用'

type JobActionBarActionsProps = JobActionBarProps & { isBatch: boolean }

function allMatchingDisabled(
  count: number,
  loading: boolean
): JobActionDisabled {
  const blocked = loading || count === 0
  return {
    rerun: blocked,
    runTo: true,
    continue: true,
    package: blocked,
    clearPacked: blocked,
    delete: blocked,
  }
}

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
    onUpgradeWorkflow,
    onExitSelectMode,
    itemLabel = '任务',
  } = props
  const [rerunOpen, setRerunOpen] = useState(false)
  const [runToOpen, setRunToOpen] = useState(false)
  const allMatching = allMatchingCount != null
  const disabled = allMatching
    ? allMatchingDisabled(allMatchingCount, loading)
    : computeActionDisabled(jobs, loading)
  const handleRunTo = (targetKey: string, startKey?: string) =>
    onRunTo?.(targetKey, startKey)

  const runToButton = (
    <Button
      variant="outlined"
      onClick={() => setRunToOpen(true)}
      disabled={disabled.runTo}
    >
      运行到
    </Button>
  )

  return (
    <>
      <div className={styles.actions}>
        {isBatch &&
          (allMatching ? (
            <Tooltip title={ALL_MATCHING_TOOLTIP}>
              <span>
                <Button variant="outlined" disabled>
                  升级 workflow
                </Button>
              </span>
            </Tooltip>
          ) : (
            <JobActionBarUpgrade
              jobs={jobs}
              itemLabel={itemLabel}
              loading={loading}
              onUpgradeWorkflow={onUpgradeWorkflow}
            />
          ))}
        <Button
          variant="outlined"
          onClick={() => setRerunOpen(true)}
          disabled={disabled.rerun}
        >
          重跑
        </Button>
        {allMatching ? (
          <Tooltip title={ALL_MATCHING_TOOLTIP}>
            <span>{runToButton}</span>
          </Tooltip>
        ) : (
          runToButton
        )}
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
    </>
  )
}
