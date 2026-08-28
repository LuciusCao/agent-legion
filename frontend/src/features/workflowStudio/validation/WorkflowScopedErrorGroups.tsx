import { parseCompareErrors } from './workflowStudioErrors'
import { WorkflowScopedErrorItem } from './WorkflowScopedErrorItem'
import type { components } from '../../../generated/api'
import styles from './WorkflowValidationPanelGroups.module.css'

type CompareError = components['schemas']['WorkflowDraftCompareError']

type Props = {
  errors: CompareError[]
  onSelectNode?: (nodeKey: string) => void
}

export function WorkflowScopedErrorGroups({ errors, onSelectNode }: Props) {
  return (
    <>
      {parseCompareErrors(errors, onSelectNode).map((group) => (
        <div key={group.category} className={styles.group}>
          <h3 className={styles.groupTitle}>{group.categoryLabel}</h3>
          <ul className={styles.list}>
            {group.items.map((item, index) => (
              <WorkflowScopedErrorItem
                key={`${group.category}-${index}`}
                item={item}
              />
            ))}
          </ul>
        </div>
      ))}
    </>
  )
}
