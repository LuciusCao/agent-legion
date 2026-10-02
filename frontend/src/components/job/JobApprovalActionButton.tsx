import type { JobSummary } from '../../types'
import { canApproveJob } from '../jobActionEligibility'
import { LabeledIconButton } from '../LabeledIconButton'

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
    <LabeledIconButton
      icon="pending_actions"
      label="审批"
      color="secondary"
      disabled={loading}
      onClick={onOpenApproval}
    />
  )
}
