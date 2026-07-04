import { useState } from 'react'
import { Button } from '@mui/material'
import type { JobSummary } from '../types'
import { BatchUpgradeDialog } from './BatchUpgradeDialog'

export type JobActionBarUpgradeProps = {
  jobs: JobSummary[]
  itemLabel?: string
  onUpgradeWorkflow?: (jobIds: string[]) => void | Promise<void>
}

export function canUpgradeJob(job: JobSummary): boolean {
  return job.is_workflow_outdated === true && job.status !== 'running'
}

export function JobActionBarUpgrade({
  jobs,
  itemLabel = '任务',
  onUpgradeWorkflow,
}: JobActionBarUpgradeProps) {
  const [open, setOpen] = useState(false)
  const disabled = jobs.length === 0 || !jobs.some((job) => canUpgradeJob(job))

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
