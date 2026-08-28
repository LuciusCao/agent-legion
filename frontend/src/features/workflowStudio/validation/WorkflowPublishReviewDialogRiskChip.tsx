import { Chip } from '@mui/material'
import { severityLabel, severityVariant } from './workflowStudioChanges'

type Props = {
  severity: 'info' | 'warning' | 'breaking'
}

export function WorkflowPublishReviewDialogRiskChip({ severity }: Props) {
  return (
    <Chip
      label={severityLabel(severity)}
      color={severityVariant(severity)}
      size="small"
    />
  )
}
