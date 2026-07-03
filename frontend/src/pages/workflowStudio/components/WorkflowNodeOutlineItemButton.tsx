import type {
  WorkflowDefinitionRecord,
  WorkflowNodeRecord,
} from '../../../types'
import { WorkflowNodeOutlineBadges } from './WorkflowNodeOutlineBadges'
import { WorkflowNodeOutlineItemDetails } from './WorkflowNodeOutlineItemDetails'
import itemStyles from '../WorkflowNodeOutlineItem.module.css'
import contentStyles from '../WorkflowNodeOutlineItemContent.module.css'

type Props = {
  workflow: WorkflowDefinitionRecord
  node: WorkflowNodeRecord
  selected: boolean
  changedNodeKeys?: Set<string>
  onSelect: (nodeKey: string) => void
}

export function WorkflowNodeOutlineItemButton({
  workflow,
  node,
  selected,
  changedNodeKeys,
  onSelect,
}: Props) {
  return (
    <button
      type="button"
      className={itemStyles.item}
      aria-pressed={selected}
      onClick={() => onSelect(node.key)}
    >
      <span className={contentStyles.row}>
        <span className={contentStyles.label}>{node.label}</span>
        <WorkflowNodeOutlineBadges
          workflow={workflow}
          nodeKey={node.key}
          changedNodeKeys={changedNodeKeys}
        />
      </span>
      <WorkflowNodeOutlineItemDetails node={node} />
    </button>
  )
}
