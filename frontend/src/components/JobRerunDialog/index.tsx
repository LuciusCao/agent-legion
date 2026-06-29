import { useState } from 'react'
import { Dialog } from '@mui/material'
import type { JobSummary, WorkflowDefinitionRecord } from '../../types'
import { type WorkflowNodesByKey } from '../../lib/workflowNodes'
import { useJobRerunDialog } from './useJobRerunDialog'
import { JobRerunDialogContent } from './JobRerunDialogContent'

export type { WorkflowNodesByKey }

export type JobRerunDialogProps = {
  open: boolean
  jobs: JobSummary[]
  workflowDefinition?: WorkflowDefinitionRecord | null
  workflowNodesByKey?: WorkflowNodesByKey | null
  itemLabel?: string
  allowFailedNodeMode?: boolean
  onConfirm: (nodeKey: string | null, fromFailedNode: boolean) => void | Promise<void>
  onClose: () => void
}

export function JobRerunDialog({
  open,
  jobs,
  workflowDefinition,
  workflowNodesByKey,
  itemLabel = '任务',
  allowFailedNodeMode = false,
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
  } = useJobRerunDialog({ jobs, workflowDefinition, workflowNodesByKey })

  const [loading, setLoading] = useState(false)

  if (!open) return null

  const handleConfirm = async () => {
    if (failedMode) {
      setLoading(true)
      try {
        await onConfirm(null, true)
      } finally {
        setLoading(false)
      }
      onClose()
      return
    }
    if (!effectiveNodeKey) return
    setLoading(true)
    try {
      await onConfirm(effectiveNodeKey, false)
    } finally {
      setLoading(false)
    }
    onClose()
  }

  const canConfirm = failedMode ? failedJobs.length > 0 : !!effectiveNodeKey

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
        canConfirm={canConfirm}
        loading={loading}
        onConfirm={handleConfirm}
        onClose={onClose}
      />
    </Dialog>
  )
}
