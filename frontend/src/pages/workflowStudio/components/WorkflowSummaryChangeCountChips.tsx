import { Chip } from '@mui/material'
import type { ChangeSummaryViewModel } from '../workflowStudioChanges'
import {
  edgeChangeLabel,
  metadataChangeLabel,
  nodeChangeLabel,
} from './WorkflowSummaryChangeCountChips.helpers'
type Props = { summary: ChangeSummaryViewModel }

export function WorkflowSummaryChangeCountChips({ summary }: Props) {
  return (
    <>
      <Chip label={nodeChangeLabel(summary)} size="small" variant="outlined" />
      <Chip label={edgeChangeLabel(summary)} size="small" variant="outlined" />
      {metadataChangeLabel(summary) !== '' && (
        <Chip
          label={metadataChangeLabel(summary)}
          size="small"
          variant="outlined"
        />
      )}
    </>
  )
}
