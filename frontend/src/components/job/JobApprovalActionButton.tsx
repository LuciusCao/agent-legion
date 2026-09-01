import { IconButton } from '@mui/material'
import type { JobSummary } from '../../types'
import { canApproveJob } from '../jobActionEligibility'
import { MaterialIcon } from '../MaterialIcon'

export function JobApprovalActionButton({
  jobs,
  loading,
  onOpenApproval,
}: {
  jobs: JobSummary[]
  loading: boolean
  onOpenApproval?: () => void
}) {
  if (!onOpenApproval || !jobs.some((job) => canApproveJob(job))) return null
  return (
    <IconButton
      aria-label="审批"
      title="审批"
      color="secondary"
      disabled={loading}
      onClick={onOpenApproval}
    >
      <MaterialIcon name="pending_actions" />
    </IconButton>
  )
}
