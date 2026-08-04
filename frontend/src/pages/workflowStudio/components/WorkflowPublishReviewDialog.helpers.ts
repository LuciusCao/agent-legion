import type { ChangeSummaryViewModel } from '../workflowStudioChanges'

export function hasCompareSummaryChanges(
  summary: ChangeSummaryViewModel | null
): boolean {
  if (!summary) return false
  return (
    summary.nodeChanges.length > 0 ||
    summary.edgeChanges.length > 0 ||
    summary.intakeChanges.length > 0 ||
    summary.metadataChanges.length > 0 ||
    summary.riskFlags.length > 0
  )
}
