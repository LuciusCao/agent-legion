import { WorkflowStudioSummaryBarActions } from './WorkflowStudioSummaryBarActions'
import { WorkflowStudioSummaryBarMeta } from './WorkflowStudioSummaryBarMeta'
import { WorkflowStudioSummaryBarTitle } from './WorkflowStudioSummaryBarTitle'
import type { StudioSummaryBarProps } from './workflowStudioSummaryBarProps'
import styles from './WorkflowStudioSummaryBar.module.css'

export function WorkflowStudioSummaryBar({
  workflow,
  revision,
  compareSummary,
  compareState,
  dirty,
  actionState,
  canSubmit,
  canPublish,
  onValidate,
  onPublish,
  onReset,
}: StudioSummaryBarProps) {
  return (
    <section aria-label="Workflow summary" className={styles.bar}>
      <WorkflowStudioSummaryBarTitle workflow={workflow} />
      <WorkflowStudioSummaryBarMeta
        revision={revision}
        dirty={dirty}
        compareSummary={compareSummary}
        compareState={compareState}
      />
      <WorkflowStudioSummaryBarActions
        actionState={actionState}
        canSubmit={canSubmit}
        canPublish={canPublish}
        dirty={dirty}
        onValidate={onValidate}
        onPublish={onPublish}
        onReset={onReset}
      />
    </section>
  )
}
