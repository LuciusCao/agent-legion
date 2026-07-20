import { useState } from 'react'
import { Button } from '@mui/material'
import type { JobSummary, WorkflowDefinitionRecord } from '../types'
import { JobRerunDialog, type WorkflowNodesByKey } from './JobRerunDialog'
import { JobRunToDialog } from './JobRunToDialog'
import { JobActionBarUpgrade } from './JobActionBarUpgrade'
import styles from './JobActionBar.module.css'

export type JobActionBarFilter = {
  key: string
  label: string
  onClick: () => void
}

export type JobActionBarProps = {
  jobs: JobSummary[]
  selectedCount?: number
  workflowDefinition?: WorkflowDefinitionRecord | null
  workflowNodesByKey?: WorkflowNodesByKey | null
  mode?: 'batch' | 'single'
  loading?: boolean
  filters?: JobActionBarFilter[]
  onExitSelectMode?: () => void
  onRerun: (
    nodeKey: string | null,
    fromFailedNode?: boolean,
    jobIds?: string[]
  ) => void
  onRunTo?: (targetKey: string, startKey?: string) => void | Promise<void>
  onContinue?: () => void | Promise<void>
  onPackage: () => void | Promise<void>
  onDelete: () => void | Promise<void>
  onUpgradeWorkflow?: (jobIds: string[]) => void | Promise<void>
  itemLabel?: string
}

export function canRerunJob(status: string): boolean {
  return status !== 'running'
}

export function canPackageJob(job: JobSummary): boolean {
  return job.status === 'completed' && !job.packed
}

export function canDeleteJob(): boolean {
  return true
}

export function canContinueJob(job: JobSummary): boolean {
  return (
    job.status === 'paused' &&
    job.execution_control?.pause_reason === 'target_reached'
  )
}

export function JobActionBar({
  jobs,
  selectedCount,
  workflowDefinition,
  workflowNodesByKey,
  mode = jobs.length > 1 ? 'batch' : 'single',
  loading = false,
  filters,
  onExitSelectMode,
  onRerun,
  onRunTo,
  onContinue,
  onPackage,
  onDelete,
  onUpgradeWorkflow,
  itemLabel = '任务',
}: JobActionBarProps) {
  const [rerunOpen, setRerunOpen] = useState(false)
  const [runToOpen, setRunToOpen] = useState(false)
  const isBatch = mode === 'batch'
  const count = selectedCount ?? jobs.length

  const rerunDisabled =
    jobs.length === 0 ||
    loading ||
    jobs.every((job) => !canRerunJob(job.status))

  const runToDisabled =
    jobs.length === 0 ||
    loading ||
    jobs.every((job) => !canRerunJob(job.status))

  const continueDisabled =
    jobs.length === 0 || loading || !jobs.some((job) => canContinueJob(job))

  const packageDisabled =
    jobs.length === 0 || loading || jobs.every((job) => !canPackageJob(job))

  const deleteDisabled = jobs.length === 0 || loading
  const handleRunTo = (targetKey: string, startKey?: string) =>
    onRunTo?.(targetKey, startKey)

  return (
    <div className={styles.actionBar} data-testid="job-action-bar">
      {isBatch && (
        <div className={styles.batchHeader}>
          <span className={styles.count}>已选择 {count} 项</span>
          {filters && filters.length > 0 && (
            <div className={styles.filters}>
              {filters.map((filter) => (
                <Button
                  key={filter.key}
                  variant="text"
                  onClick={filter.onClick}
                >
                  {filter.label}
                </Button>
              ))}
            </div>
          )}
        </div>
      )}

      <div className={styles.actions}>
        {isBatch && (
          <JobActionBarUpgrade
            jobs={jobs}
            itemLabel={itemLabel}
            loading={loading}
            onUpgradeWorkflow={onUpgradeWorkflow}
          />
        )}
        <Button
          variant="outlined"
          onClick={() => setRerunOpen(true)}
          disabled={rerunDisabled}
        >
          重跑
        </Button>
        <Button
          variant="outlined"
          onClick={() => setRunToOpen(true)}
          disabled={runToDisabled}
        >
          运行到
        </Button>
        {!isBatch && jobs.some((job) => canContinueJob(job)) && (
          <Button
            variant="outlined"
            onClick={onContinue}
            disabled={continueDisabled}
          >
            继续完整流程
          </Button>
        )}
        <Button
          variant="outlined"
          onClick={onPackage}
          disabled={packageDisabled}
        >
          打包
        </Button>
        <Button
          variant="outlined"
          color="error"
          onClick={onDelete}
          disabled={deleteDisabled}
        >
          删除
        </Button>
        {isBatch && onExitSelectMode && (
          <Button variant="outlined" onClick={onExitSelectMode}>
            退出
          </Button>
        )}
      </div>

      <JobRerunDialog
        open={rerunOpen}
        jobs={jobs}
        workflowDefinition={workflowDefinition}
        workflowNodesByKey={workflowNodesByKey}
        itemLabel={itemLabel}
        allowFailedNodeMode={mode === 'batch'}
        onClose={() => setRerunOpen(false)}
        onConfirm={onRerun}
      />
      <JobRunToDialog
        open={runToOpen}
        jobs={jobs}
        workflowDefinition={workflowDefinition}
        workflowNodesByKey={workflowNodesByKey}
        itemLabel={itemLabel}
        onClose={() => setRunToOpen(false)}
        onConfirm={handleRunTo}
      />
    </div>
  )
}
