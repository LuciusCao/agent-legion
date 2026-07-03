import type { WorkflowRevisionSummary } from '../../types'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'
import {
  computeSummaryStatus,
  SummaryStatus,
} from './workflowStudioSummaryMeta'

export function computeSummaryBarMeta(
  revision: WorkflowRevisionSummary | null,
  dirty: boolean,
  compareSummary: ChangeSummaryViewModel | null,
  compareState: 'idle' | 'loading' | 'ready' | 'error'
): { hash: string; status: SummaryStatus } {
  const hash = revision?.definition_hash?.slice(0, 8) ?? '--------'
  const hasChanges = Boolean(
    compareSummary &&
    (compareSummary.nodeChanges.length > 0 ||
      compareSummary.edgeChanges.length > 0 ||
      compareSummary.intakeChanges.length > 0 ||
      compareSummary.metadataChanges.length > 0 ||
      compareSummary.riskFlags.length > 0)
  )
  const status = computeSummaryStatus(
    dirty,
    compareState,
    hasChanges,
    compareSummary?.riskLevel === 'breaking'
  )
  return { hash, status }
}
