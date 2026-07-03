import { Chip } from '@mui/material'
import type { WorkflowRevisionSummary } from '../../types'
import type { SummaryStatus } from './workflowStudioSummaryMeta'

type Props = {
  revision: WorkflowRevisionSummary | null
  hash: string
  status: SummaryStatus
}

export function WorkflowStudioSummaryBarMetaStatus({
  revision,
  hash,
  status,
}: Props) {
  return (
    <>
      <Chip
        label={revision ? `v${revision.version}` : '无 active revision'}
        size="small"
        variant="outlined"
      />
      <Chip label={hash} size="small" variant="outlined" />
      <Chip
        label={status.label}
        size="small"
        color={status.color}
        variant={status.filled ? 'filled' : 'outlined'}
      />
    </>
  )
}
