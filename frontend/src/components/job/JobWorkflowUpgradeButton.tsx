import { IconButton } from '@mui/material'
import type { JobSummary } from '../../types'
import { MaterialIcon } from '../MaterialIcon'

export function JobWorkflowUpgradeButton({
  jobs,
  loading,
  onUpgradeWorkflow,
}: {
  jobs: JobSummary[]
  loading: boolean
  onUpgradeWorkflow: () => void | Promise<void>
}) {
  const disabled =
    jobs.length !== 1 ||
    loading ||
    !jobs[0].is_workflow_outdated ||
    jobs[0].status === 'running'

  return (
    <IconButton
      aria-label="升级 workflow"
      title="升级 workflow"
      disabled={disabled}
      onClick={onUpgradeWorkflow}
    >
      <MaterialIcon name="arrow_circle_up" />
    </IconButton>
  )
}
