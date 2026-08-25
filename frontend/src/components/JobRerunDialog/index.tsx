import { useState } from 'react'
import { Dialog } from '@mui/material'
import type { JobSummary } from '../../types'
import type { NodeCatalog } from '../../lib/nodeCatalog'
import { type WorkflowNodesByKey } from '../../lib/workflowNodes'
import { useJobRerunDialog } from './useJobRerunDialog'
import { useFailureCategories } from './useFailureCategories'
import type {
  FailureCategoryContext,
  JobRerunConfirmArgs,
} from './useFailureCategories'
import { JobRerunDialogContent } from './JobRerunDialogContent'

export type { WorkflowNodesByKey }

export type JobRerunDialogProps = {
  open: boolean
  jobs: JobSummary[]
  workflowDefinition?: NodeCatalog | null
  workflowNodesByKey?: WorkflowNodesByKey | null
  itemLabel?: string
  allowFailedNodeMode?: boolean
  failureContext?: FailureCategoryContext
  onConfirm: (...args: JobRerunConfirmArgs) => void | Promise<void>
  onClose: () => void
}

export function JobRerunDialog({
  open,
  jobs,
  workflowDefinition,
  workflowNodesByKey,
  itemLabel = '任务',
  allowFailedNodeMode = false,
  failureContext,
  onConfirm,
  onClose,
}: JobRerunDialogProps) {
  const {
    setSelectedNodeKey,
    failedMode,
    setFailedMode,
    effectiveNodeKey,
    failedJobs,
    nonFailedJobs,
    excluded,
    runnableJobs,
    notStartedJobs,
    runningJobs,
  } = useJobRerunDialog({ jobs, workflowDefinition, workflowNodesByKey })
  const failure = useFailureCategories(failedMode, failureContext, failedJobs)

  const [loading, setLoading] = useState(false)

  if (!open) return null

  const handleConfirm = async () => {
    if (!failedMode && !effectiveNodeKey) return
    setLoading(true)
    try {
      if (failedMode) {
        await onConfirm(...failure.confirmArgs())
      } else if (allowFailedNodeMode) {
        await onConfirm(
          effectiveNodeKey,
          false,
          runnableJobs.map((job) => job.id)
        )
      } else {
        await onConfirm(effectiveNodeKey, false)
      }
    } catch {
      // Keep the dialog open on failure; the store already surfaced a toast.
      return
    } finally {
      setLoading(false)
    }
    onClose()
  }

  const canConfirm = failedMode
    ? failure.canConfirm
    : !!effectiveNodeKey && (!allowFailedNodeMode || runnableJobs.length > 0)

  return (
    <Dialog
      open
      onClose={onClose}
      PaperProps={{
        sx: {
          minWidth: '520px',
          maxWidth: '760px',
          width: 'min(760px, 92vw)',
        },
      }}
    >
      <JobRerunDialogContent
        jobs={jobs}
        workflowDefinition={workflowDefinition}
        workflowNodesByKey={workflowNodesByKey}
        itemLabel={itemLabel}
        allowFailedNodeMode={allowFailedNodeMode}
        failedMode={failedMode}
        setFailedMode={setFailedMode}
        effectiveNodeKey={effectiveNodeKey}
        setSelectedNodeKey={setSelectedNodeKey}
        failedJobs={failedJobs}
        nonFailedJobs={nonFailedJobs}
        excluded={excluded}
        runnableJobs={runnableJobs}
        notStartedJobs={notStartedJobs}
        runningJobs={runningJobs}
        failure={failure}
        canConfirm={canConfirm}
        loading={loading}
        onConfirm={handleConfirm}
        onClose={onClose}
      />
    </Dialog>
  )
}
