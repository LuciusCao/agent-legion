import { useState } from 'react'
import { IconButton } from '@mui/material'
import type { JobSummary, UpgradeMode } from '../../types'
import { MaterialIcon } from '../MaterialIcon'
import { JobWorkflowUpgradeDialog } from './JobWorkflowUpgradeDialog'

export function JobWorkflowUpgradeButton({
  jobs,
  loading,
  onUpgradeWorkflow,
}: {
  jobs: JobSummary[]
  loading: boolean
  onUpgradeWorkflow: (mode: UpgradeMode) => void | Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const disabled =
    jobs.length !== 1 ||
    loading ||
    !jobs[0].is_workflow_outdated ||
    jobs[0].status === 'running'

  return (
    <>
      <IconButton
        aria-label="升级 workflow"
        title="升级 workflow"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        <MaterialIcon name="arrow_circle_up" />
      </IconButton>
      {/* Always mounted: `disabled` flips to true the moment the upgrade
          request starts (loading), and unmounting here would destroy the
          dialog's selected mode before a failed request can be retried. */}
      <JobWorkflowUpgradeDialog
        open={open}
        onClose={() => setOpen(false)}
        onConfirm={onUpgradeWorkflow}
      />
    </>
  )
}
