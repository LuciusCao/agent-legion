import { Chip } from '@mui/material'
import type { WorkflowDefinitionRecord } from '../../../types'
import {
  isBranchNode,
  isEntryNode,
  isTerminalNode,
} from '../workflowStudioTopology'
import styles from '../WorkflowNodeOutlineBadges.module.css'
import { WORKFLOW_NODE_OUTLINE_BADGE_LABELS } from './workflowNodeOutlineBadgeLabels'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  nodeKey: string
  changedNodeKeys?: Set<string>
}

export function WorkflowNodeOutlineBadges({
  workflow,
  nodeKey,
  changedNodeKeys,
}: Props) {
  const badges: string[] = []
  if (isEntryNode(workflow, nodeKey)) badges.push('entry')
  if (isBranchNode(workflow, nodeKey)) badges.push('branch')
  if (isTerminalNode(workflow, nodeKey)) badges.push('terminal')
  if (changedNodeKeys?.has(nodeKey)) badges.push('changed')
  if (badges.length === 0) return null
  return (
    <span className={styles.badges}>
      {badges.map((badge) => (
        <Chip
          key={badge}
          label={WORKFLOW_NODE_OUTLINE_BADGE_LABELS[badge] ?? badge}
          size="small"
          color={badge === 'changed' ? 'warning' : 'default'}
          variant={badge === 'changed' ? 'filled' : 'outlined'}
          className={styles.badge}
        />
      ))}
    </span>
  )
}
