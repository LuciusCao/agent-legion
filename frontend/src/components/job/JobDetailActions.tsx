import { useState } from 'react'
import type { JobSummary } from '../../types'
import type { NodeCatalog } from '../../lib/nodeCatalog'
import { JobRerunDialog, type WorkflowNodesByKey } from '../JobRerunDialog'
import { JobRunToDialog } from './JobRunToDialog'
import { JobDeleteDialog } from './JobDeleteDialog'
import { LabeledIconButton } from '../LabeledIconButton'
import { canContinueJob, computeActionDisabled } from '../jobActionEligibility'
import { JobApprovalActionButton } from './JobApprovalActionButton'
import { JobWorkflowUpgradeButton } from './JobWorkflowUpgradeButton'
import styles from './JobDetailActions.module.css'

export type JobDetailActionsProps = {
  jobs: JobSummary[]
  workflowDefinition?: NodeCatalog | null
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
  onOpenApproval?: () => void
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
  onOpenApproval,
}: JobDetailActionsProps) {
  const [rerunOpen, setRerunOpen] = useState(false)
  const [runToOpen, setRunToOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)

  const disabled = computeActionDisabled(jobs, loading)

  const showContinue = jobs.some((job) => canContinueJob(job))

  return (
    <>
      <div className={styles.actions} data-testid="job-detail-actions">
        <JobApprovalActionButton
          jobs={jobs}
          loading={loading}
          onOpenApproval={onOpenApproval}
        />
        <LabeledIconButton
          icon="restart_alt"
          label="重跑"
          tooltip="从选定的节点开始，一路重新执行到流程结束"
          disabled={disabled.rerun}
          onClick={() => setRerunOpen(true)}
        />
        {onUpgradeWorkflow && (
          <JobWorkflowUpgradeButton
            jobs={jobs}
            loading={loading}
            onUpgradeWorkflow={onUpgradeWorkflow}
          />
        )}
        <LabeledIconButton
          icon="play_circle"
          label="运行到节点"
          ariaLabel="运行到"
          tooltip="只执行到你选定的节点就暂停，后面的节点不会自动跑；之后可点「继续」跑完剩余流程"
          disabled={disabled.runTo}
          onClick={() => setRunToOpen(true)}
        />
        {showContinue && onContinue && (
          <LabeledIconButton
            icon="skip_next"
            label="继续"
            ariaLabel="继续完整流程"
            tooltip="接着「运行到节点」停下的位置，把剩余节点全部跑完"
            disabled={disabled.continue}
            onClick={onContinue}
          />
        )}
        <LabeledIconButton
          icon="inventory_2"
          label="打包"
          disabled={disabled.package}
          onClick={onPackage}
        />
        {onClearPacked && (
          <LabeledIconButton
            icon="unarchive"
            label="清空打包"
            ariaLabel="清空打包状态"
            disabled={loading || !jobs.some((job) => job.packed)}
            onClick={onClearPacked}
          />
        )}
        <LabeledIconButton
          icon="delete"
          label="删除"
          color="error"
          disabled={disabled.delete}
          onClick={() => setDeleteOpen(true)}
        />
        <LabeledIconButton
          icon="folder_open"
          label="产物"
          ariaLabel="产物文件"
          disabled={loading}
          onClick={onOpenArtifacts}
        />
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
