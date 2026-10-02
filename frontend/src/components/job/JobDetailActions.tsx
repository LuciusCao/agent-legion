import { useState } from 'react'
import type { JobSummary, UpgradeMode } from '../../types'
import type { NodeCatalog } from '../../lib/nodeCatalog'
import { JobRerunDialog, type WorkflowNodesByKey } from '../JobRerunDialog'
import { JobRunToDialog } from './JobRunToDialog'
import { JobDeleteDialog } from './JobDeleteDialog'
import {
  canContinueJob,
  canApproveJob,
  computeActionDisabled,
} from '../jobActionEligibility'
import { JobDetailToolbar, type JobToolbarAction } from './JobDetailToolbar'
import { JobWorkflowUpgradeDialog } from './JobWorkflowUpgradeDialog'

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
  onUpgradeWorkflow?: (mode: UpgradeMode) => void | Promise<void>
  onOpenApproval?: () => void
  /** 唤起排查 Dock（#795 PR③）：job detail 头部入口，job 级上下文。 */
  onOpenDiagnosis?: () => void
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
  onOpenDiagnosis,
}: JobDetailActionsProps) {
  const [rerunOpen, setRerunOpen] = useState(false)
  const [runToOpen, setRunToOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [upgradeOpen, setUpgradeOpen] = useState(false)

  const disabled = computeActionDisabled(jobs, loading)

  const showContinue = jobs.some((job) => canContinueJob(job))

  const execution: JobToolbarAction[] = [
    {
      icon: 'restart_alt',
      label: '重跑',
      tooltip: '从选定的节点开始，一路重新执行到流程结束',
      disabled: disabled.rerun,
      onClick: () => setRerunOpen(true),
    },
    {
      icon: 'play_circle',
      label: '运行到节点',
      ariaLabel: '运行到',
      tooltip: '只执行到选定节点就暂停，之后点「继续」跑完剩余流程',
      disabled: disabled.runTo,
      onClick: () => setRunToOpen(true),
    },
  ]
  if (onOpenApproval && jobs.some(canApproveJob))
    execution.unshift({
      icon: 'pending_actions',
      label: '审批',
      color: 'secondary',
      disabled: loading,
      onClick: onOpenApproval,
    })
  if (onUpgradeWorkflow && jobs.length === 1 && jobs[0].is_workflow_outdated)
    execution.splice(1, 0, {
      icon: 'arrow_circle_up',
      label: '升级',
      ariaLabel: '升级 workflow',
      disabled: loading || jobs[0].status === 'running',
      onClick: () => setUpgradeOpen(true),
    })
  if (showContinue && onContinue)
    execution.push({
      icon: 'skip_next',
      label: '继续',
      ariaLabel: '继续完整流程',
      tooltip: '把剩余节点全部跑完',
      disabled: disabled.continue,
      onClick: onContinue,
    })
  const secondary: JobToolbarAction[] = [
    {
      icon: 'inventory_2',
      label: '打包',
      disabled: disabled.package,
      onClick: onPackage,
    },
    {
      icon: 'folder_open',
      label: '产物',
      ariaLabel: '产物文件',
      disabled: loading,
      onClick: onOpenArtifacts,
    },
    {
      icon: 'delete',
      label: '删除',
      color: 'error',
      disabled: disabled.delete,
      onClick: () => setDeleteOpen(true),
    },
  ]
  if (onClearPacked)
    secondary.splice(1, 0, {
      icon: 'unarchive',
      label: '清空打包',
      ariaLabel: '清空打包状态',
      disabled: loading || !jobs.some((job) => job.packed),
      onClick: onClearPacked,
    })

  return (
    <>
      <JobDetailToolbar
        execution={execution}
        secondary={secondary}
        onOpenDiagnosis={onOpenDiagnosis}
      />
      <JobWorkflowUpgradeDialog
        open={upgradeOpen}
        onClose={() => setUpgradeOpen(false)}
        onConfirm={onUpgradeWorkflow ?? (() => {})}
      />

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
