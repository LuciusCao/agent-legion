import { WorkflowSummaryChangeChips } from './components/WorkflowSummaryChangeChips'
import { computeSummaryBarMeta } from './workflowStudioSummaryBarMeta.helpers'
import { WorkflowStudioSummaryBarMetaStatus } from './WorkflowStudioSummaryBarMetaStatus'
import type { StudioSummaryBarMetaProps } from './workflowStudioSummaryBarMetaProps'
import styles from './WorkflowStudioSummaryBar.module.css'

export function WorkflowStudioSummaryBarMeta({
  revision,
  dirty,
  compareSummary,
  compareState,
}: StudioSummaryBarMetaProps) {
  const { hash, status } = computeSummaryBarMeta(
    revision,
    dirty,
    compareSummary,
    compareState
  )
  return (
    <div className={styles.meta}>
      <WorkflowStudioSummaryBarMetaStatus
        revision={revision}
        hash={hash}
        status={status}
      />
      <WorkflowSummaryChangeChips summary={compareSummary} />
    </div>
  )
}
