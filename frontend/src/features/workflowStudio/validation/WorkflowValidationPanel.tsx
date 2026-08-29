import { groupValidationErrors } from '../shared/workflowStudioModel'
import { WorkflowScopedErrorGroups } from './WorkflowScopedErrorGroups'
import { WorkflowValidationPanelMessage } from './WorkflowValidationPanelMessage'
import { WorkflowValidationPanelStringGroups } from './WorkflowValidationPanelStringGroups'
import type { components } from '../../../generated/api'
import styles from './WorkflowValidationPanel.module.css'

type CompareError = components['schemas']['WorkflowDraftCompareError']

type Props = {
  message: string
  errors: string[]
  compareErrors?: CompareError[]
  onSelectNode?: (nodeKey: string) => void
}

export function WorkflowValidationPanel({
  message,
  errors,
  compareErrors,
  onSelectNode,
}: Props) {
  const groups = groupValidationErrors(errors)
  if (!message && errors.length === 0 && (compareErrors ?? []).length === 0)
    return null
  return (
    <section aria-label="Workflow validation" className={styles.panel}>
      {message && <WorkflowValidationPanelMessage message={message} />}
      <WorkflowValidationPanelStringGroups groups={groups} />
      {(compareErrors ?? []).length > 0 && (
        <WorkflowScopedErrorGroups
          errors={compareErrors!}
          onSelectNode={onSelectNode}
        />
      )}
    </section>
  )
}
