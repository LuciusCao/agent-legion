import type {
  WorkflowDefinitionRecord,
  WorkflowRevisionSummary,
} from '../../types'
import styles from './WorkflowStudioSummaryBar.module.css'
import { WorkflowStudioSummaryBarActions } from './WorkflowStudioSummaryBarActions'
import { WorkflowStudioSummaryBarMeta } from './WorkflowStudioSummaryBarMeta'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  revision: WorkflowRevisionSummary | null
  dirty: boolean
  actionState: 'idle' | 'validating' | 'publishing'
  canSubmit: boolean
  onValidate: () => void
  onPublish: () => void
  onReset: () => void
}

export function WorkflowStudioSummaryBar({
  workflow,
  revision,
  dirty,
  actionState,
  canSubmit,
  onValidate,
  onPublish,
  onReset,
}: Props) {
  return (
    <section aria-label="Workflow summary" className={styles.bar}>
      <div className={styles.titleBlock}>
        <h1 className={styles.title}>{workflow?.label ?? '工作流'}</h1>
        <p className={styles.subtitle}>{workflow?.key ?? '未加载'}</p>
      </div>
      <WorkflowStudioSummaryBarMeta revision={revision} dirty={dirty} />
      <WorkflowStudioSummaryBarActions
        actionState={actionState}
        canSubmit={canSubmit}
        dirty={dirty}
        onValidate={onValidate}
        onPublish={onPublish}
        onReset={onReset}
      />
    </section>
  )
}
