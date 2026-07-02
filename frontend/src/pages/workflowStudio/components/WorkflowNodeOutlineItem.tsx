import type { WorkflowDefinitionRecord } from '../../../types'
import { WorkflowNodeOutlineItemButton } from './WorkflowNodeOutlineItemButton'

type Props = {
  workflow: WorkflowDefinitionRecord
  nodeKey: string
  selected: boolean
  changedNodeKeys?: Set<string>
  onSelect: (nodeKey: string) => void
}

export function WorkflowNodeOutlineItem({
  workflow,
  nodeKey,
  selected,
  changedNodeKeys,
  onSelect,
}: Props) {
  const node = workflow.nodes.find((candidate) => candidate.key === nodeKey)
  if (!node) return null
  return (
    <li key={node.key}>
      <WorkflowNodeOutlineItemButton
        workflow={workflow}
        node={node}
        selected={selected}
        changedNodeKeys={changedNodeKeys}
        onSelect={onSelect}
      />
    </li>
  )
}
