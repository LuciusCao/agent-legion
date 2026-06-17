import { useState } from 'react'
import type { JobSummary, WorkflowDefinitionRecord } from '../types'
import { JobRerunDialog, type WorkflowNodesByKey } from './JobRerunDialog'
import { JobRunToDialog } from './JobRunToDialog'
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
  onRerun: (nodeKey: string) => void | Promise<void>
  onRunTo?: (targetKey: string, startKey?: string) => void | Promise<void>
  onContinue?: () => void | Promise<void>
  onPackage: () => void | Promise<void>
  onDelete: () => void | Promise<void>
  itemLabel?: string
}

export function canRerunJob(status: string): boolean {
  return status !== 'running'
}

export function canPackageJob(status: string): boolean {
  return status === 'completed'
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
    jobs.length === 0 ||
    loading ||
    jobs.every((job) => !canPackageJob(job.status))

  const deleteDisabled = jobs.length === 0 || loading

  const handleRerun = async (nodeKey: string) => {
    await onRerun(nodeKey)
  }

  const handleRunTo = async (targetKey: string, startKey?: string) => {
    await onRunTo?.(targetKey, startKey)
  }

  const handleContinue = async () => {
    await onContinue?.()
  }

  return (
    <div className={styles.actionBar} data-testid="job-action-bar">
      {isBatch && (
        <div className={styles.batchHeader}>
          <span className={styles.count}>已选择 {count} 项</span>
          {filters && filters.length > 0 && (
            <div className={styles.filters}>
              {filters.map((filter) => (
                <md-text-button key={filter.key} onClick={filter.onClick}>
                  {filter.label}
                </md-text-button>
              ))}
            </div>
          )}
        </div>
      )}

      <div className={styles.actions}>
        <md-outlined-button
          onClick={() => setRerunOpen(true)}
          disabled={rerunDisabled || undefined}
        >
          重跑
        </md-outlined-button>
        <md-outlined-button
          onClick={() => setRunToOpen(true)}
          disabled={runToDisabled || undefined}
        >
          运行到
        </md-outlined-button>
        {!isBatch && jobs.some((job) => canContinueJob(job)) && (
          <md-outlined-button
            onClick={handleContinue}
            disabled={continueDisabled || undefined}
          >
            继续完整流程
          </md-outlined-button>
        )}
        <md-outlined-button
          onClick={onPackage}
          disabled={packageDisabled || undefined}
        >
          打包
        </md-outlined-button>
        <md-outlined-button
          onClick={onDelete}
          disabled={deleteDisabled || undefined}
          style={{ color: 'var(--md-sys-color-error)' }}
        >
          删除
        </md-outlined-button>
        {isBatch && onExitSelectMode && (
          <md-outlined-button onClick={onExitSelectMode}>
            退出
          </md-outlined-button>
        )}
      </div>

      <JobRerunDialog
        open={rerunOpen}
        jobs={jobs}
        workflowDefinition={workflowDefinition}
        workflowNodesByKey={workflowNodesByKey}
        itemLabel={itemLabel}
        onClose={() => setRerunOpen(false)}
        onConfirm={handleRerun}
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
