import type { JobSummary } from '../../types'
import { Button } from '@mui/material'
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

export function JobBatchApprovalButtons({
  disabled,
  onOpenApproval,
}: {
  disabled: boolean
  onOpenApproval: (kind: 'approve' | 'reject') => void
}) {
  return (
    <span style={{ display: 'flex', gap: 8 }}>
      {(['approve', 'reject'] as const).map((kind) => (
        <Button
          key={kind}
          variant="outlined"
          color={kind === 'reject' ? 'error' : 'primary'}
          disabled={disabled}
          onClick={() => onOpenApproval(kind)}
        >
          {kind === 'approve' ? '通过' : '终止'}
        </Button>
      ))}
    </span>
  )
}
