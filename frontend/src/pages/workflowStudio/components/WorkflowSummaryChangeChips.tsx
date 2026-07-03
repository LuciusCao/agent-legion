import { Chip } from '@mui/material'
import type { ChangeSummaryViewModel } from '../workflowStudioChanges'
import { WorkflowSummaryChangeCountChips } from './WorkflowSummaryChangeCountChips'

type Props = { summary: ChangeSummaryViewModel | null }

export function WorkflowSummaryChangeChips({ summary }: Props) {
  if (!summary) return null
  const hasChanges =
    summary.nodeChanges.length > 0 ||
    summary.edgeChanges.length > 0 ||
    summary.intakeChanges.length > 0 ||
    summary.metadataChanges.length > 0 ||
    summary.riskFlags.length > 0
  if (!hasChanges) return null
  return (
    <>
      <WorkflowSummaryChangeCountChips summary={summary} />
      {summary.riskLevel !== 'none' && (
        <Chip
          label={`风险：${summary.severityLabel}`}
          size="small"
          color={
            summary.riskLevel === 'breaking'
              ? 'error'
              : summary.riskLevel === 'warning'
                ? 'warning'
                : 'info'
          }
          variant="filled"
        />
      )}
    </>
  )
}
