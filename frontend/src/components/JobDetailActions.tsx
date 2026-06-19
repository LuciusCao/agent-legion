import { useState } from 'react'
import { IconButton } from '@mui/material'
import type { JobSummary, WorkflowDefinitionRecord } from '../types'
import { JobRerunDialog, type WorkflowNodesByKey } from './JobRerunDialog'
import { JobRunToDialog } from './JobRunToDialog'
import { JobDeleteDialog } from './JobDeleteDialog'
import { MaterialIcon } from './MaterialIcon'
import { canRerunJob, canPackageJob, canContinueJob } from './JobActionBar'
import styles from './JobDetailActions.module.css'

export type JobDetailActionsProps = {
  jobs: JobSummary[]
  workflowDefinition?: WorkflowDefinitionRecord | null
  workflowNodesByKey?: WorkflowNodesByKey | null
  loading?: boolean
  onRerun: (nodeKey: string) => void | Promise<void>
  onRunTo?: (targetKey: string, startKey?: string) => void | Promise<void>
  onContinue?: () => void | Promise<void>
  onPackage: () => void | Promise<void>
  onDelete: () => void | Promise<void>
  onOpenArtifacts: () => void
}

export function JobDetailActions({
  jobs,
  workflowDefinition,
  workflowNodesByKey,
  loading = false,
  onRerun,
  onRunTo,
  onContinue,
  onPackage,
  onDelete,
  onOpenArtifacts,
}: JobDetailActionsProps) {
  const [rerunOpen, setRerunOpen] = useState(false)
  const [runToOpen, setRunToOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)

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

  const showContinue = jobs.some((job) => canContinueJob(job))

  return (
    <>
      <div className={styles.actions} data-testid="job-detail-actions">
        <IconButton
          aria-label="重跑"
          title="重跑"
          disabled={rerunDisabled}
          onClick={() => setRerunOpen(true)}
        >
          <MaterialIcon name="restart_alt" />
        </IconButton>
        <IconButton
          aria-label="运行到"
          title="运行到"
          disabled={runToDisabled}
          onClick={() => setRunToOpen(true)}
        >
          <MaterialIcon name="play_circle" />
        </IconButton>
        {showContinue && onContinue && (
          <IconButton
            aria-label="继续完整流程"
            title="继续完整流程"
            disabled={continueDisabled}
            onClick={handleContinue}
          >
            <MaterialIcon name="skip_next" />
          </IconButton>
        )}
        <IconButton
          aria-label="打包"
          title="打包"
          disabled={packageDisabled}
          onClick={onPackage}
        >
          <MaterialIcon name="inventory_2" />
        </IconButton>
        <IconButton
          aria-label="删除"
          title="删除"
          color="error"
          disabled={deleteDisabled}
          onClick={() => setDeleteOpen(true)}
        >
          <MaterialIcon name="delete" />
        </IconButton>
        <IconButton
          aria-label="产物文件"
          title="产物文件"
          disabled={loading}
          onClick={onOpenArtifacts}
        >
          <MaterialIcon name="folder_open" />
        </IconButton>
      </div>

      <JobRerunDialog
        open={rerunOpen}
        jobs={jobs}
        workflowDefinition={workflowDefinition}
        workflowNodesByKey={workflowNodesByKey}
        onClose={() => setRerunOpen(false)}
        onConfirm={handleRerun}
      />
      <JobRunToDialog
        open={runToOpen}
        jobs={jobs}
        workflowDefinition={workflowDefinition}
        workflowNodesByKey={workflowNodesByKey}
        onClose={() => setRunToOpen(false)}
        onConfirm={handleRunTo}
      />
      <JobDeleteDialog
        open={deleteOpen}
        title={jobs[0]?.title || jobs[0]?.source_id}
        onClose={() => setDeleteOpen(false)}
        onConfirm={async () => {
          try {
            await onDelete()
          } finally {
            setDeleteOpen(false)
          }
        }}
      />
    </>
  )
}
