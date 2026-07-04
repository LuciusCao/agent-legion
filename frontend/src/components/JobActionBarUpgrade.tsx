import { useState } from 'react'
import { Button } from '@mui/material'
import type { JobSummary } from '../types'
import { BatchUpgradeDialog } from './BatchUpgradeDialog'
import { canUpgradeJob } from './canUpgradeJob'

export type JobActionBarUpgradeProps = {
  jobs: JobSummary[]
  itemLabel?: string
  loading?: boolean
  onUpgradeWorkflow?: (jobIds: string[]) => void | Promise<void>
}

export function JobActionBarUpgrade({
  jobs,
  itemLabel = '任务',
  loading = false,
  onUpgradeWorkflow,
}: JobActionBarUpgradeProps) {
  const [open, setOpen] = useState(false)
  const disabled =
    jobs.length === 0 ||
    loading ||
    !onUpgradeWorkflow ||
    !jobs.some(canUpgradeJob)

  return (
    <>
      <Button
        variant="outlined"
        onClick={() => setOpen(true)}
        disabled={disabled}
      >
        升级 workflow
      </Button>
      {onUpgradeWorkflow && (
        <BatchUpgradeDialog
          open={open}
          jobs={jobs.map((job) => ({
            id: job.id,
            name: job.title || job.source_id || job.id,
            status: job.status,
            isWorkflowOutdated: job.is_workflow_outdated ?? false,
          }))}
          itemLabel={itemLabel}
          loading={loading}
          onClose={() => setOpen(false)}
          onConfirm={async (jobIds) => {
            await onUpgradeWorkflow(jobIds)
            setOpen(false)
          }}
        />
      )}
    </>
  )
}
