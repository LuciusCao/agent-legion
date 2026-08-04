import { useState } from 'react'
import { IconButton } from '@mui/material'
import type { JobSummary, WorkflowDefinitionRecord } from '../../types'
import { JobRerunDialog, type WorkflowNodesByKey } from '../JobRerunDialog'
import { JobRunToDialog } from './JobRunToDialog'
import { JobDeleteDialog } from './JobDeleteDialog'
import { MaterialIcon } from '../MaterialIcon'
import { canContinueJob, computeActionDisabled } from '../jobActionEligibility'
import { JobWorkflowUpgradeButton } from './JobWorkflowUpgradeButton'
import styles from './JobDetailActions.module.css'

export type JobDetailActionsProps = {
  jobs: JobSummary[]
  workflowDefinition?: WorkflowDefinitionRecord | null
  workflowNodesByKey?: WorkflowNodesByKey | null
  loading?: boolean
  onRerun: (nodeKey: string | null, fromFailedNode?: boolean) => void
  onRunTo?: (targetKey: string, startKey?: string) => void | Promise<void>
  onContinue?: () => void | Promise<void>
  onPackage: () => void | Promise<void>
  onClearPacked?: () => void | Promise<void>
  onDelete: () => void | Promise<void>
  onOpenArtifacts: () => void
  onUpgradeWorkflow?: () => void | Promise<void>
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
  onClearPacked,
  onDelete,
  onOpenArtifacts,
  onUpgradeWorkflow,
}: JobDetailActionsProps) {
  const [rerunOpen, setRerunOpen] = useState(false)
  const [runToOpen, setRunToOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)

  const disabled = computeActionDisabled(jobs, loading)

  const showContinue = jobs.some((job) => canContinueJob(job))

  return (
    <>
      <div className={styles.actions} data-testid="job-detail-actions">
        <IconButton
          aria-label="重跑"
          title="重跑"
          disabled={disabled.rerun}
          onClick={() => setRerunOpen(true)}
        >
          <MaterialIcon name="restart_alt" />
        </IconButton>
        {onUpgradeWorkflow && (
          <JobWorkflowUpgradeButton
            jobs={jobs}
            loading={loading}
            onUpgradeWorkflow={onUpgradeWorkflow}
          />
        )}
        <IconButton
          aria-label="运行到"
          title="运行到"
          disabled={disabled.runTo}
          onClick={() => setRunToOpen(true)}
        >
          <MaterialIcon name="play_circle" />
        </IconButton>
        {showContinue && onContinue && (
          <IconButton
            aria-label="继续完整流程"
            title="继续完整流程"
            disabled={disabled.continue}
            onClick={onContinue}
          >
            <MaterialIcon name="skip_next" />
          </IconButton>
        )}
        <IconButton
          aria-label="打包"
          title="打包"
          disabled={disabled.package}
          onClick={onPackage}
        >
          <MaterialIcon name="inventory_2" />
        </IconButton>
        {onClearPacked && (
          <IconButton
            aria-label="清空打包状态"
            title="清空打包状态"
            disabled={loading || !jobs.some((job) => job.packed)}
            onClick={onClearPacked}
          >
            <MaterialIcon name="unarchive" />
          </IconButton>
        )}
        <IconButton
          aria-label="删除"
          title="删除"
          color="error"
          disabled={disabled.delete}
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
        onConfirm={onRerun}
      />
      <JobRunToDialog
        open={runToOpen}
        jobs={jobs}
        workflowDefinition={workflowDefinition}
        workflowNodesByKey={workflowNodesByKey}
        onClose={() => setRunToOpen(false)}
        onConfirm={onRunTo ?? (async () => {})}
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
