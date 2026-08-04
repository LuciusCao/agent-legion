import { memo } from 'react'
import { Handle, Position, NodeProps, type Node } from '@xyflow/react'
import {
  formatDuration,
  STATUS_ICON,
  STATUS_LABEL,
  type DagNodeStatus,
} from '../dagNodeStatus'
import { ChipList } from './DagNodeChips'
import { MaterialIcon } from '../MaterialIcon'
import { DagNodeDefinitionMeta } from './DagNodeDefinitionMeta'
import { DagNodeExecutorBadge } from './DagNodeExecutorBadge'
import type { ExecutorKind } from '../../types/jobTypes'
import styles from './DagNode.module.css'

export interface DagNodeData extends Record<string, unknown> {
  label: string
  status: DagNodeStatus
  duration?: number
  executorKind?: ExecutorKind | null
  executorId?: string | null
  agentId?: string | null
  workerId?: string | null
  nodeKey?: string
  capability?: string
  topologyBadges?: Array<'entry' | 'branch' | 'terminal'>
  terminalOutcome?: string
  inputs: string[]
  outputs: string[]
}

export type DagNodeType = Node<DagNodeData, 'dagNode'>

export const DagNode = memo(function DagNode(props: NodeProps<DagNodeType>) {
  const { data, selected } = props
  const icon = STATUS_ICON[data.status]

  return (
    <div
      data-testid="dag-node"
      data-status={data.status}
      className={[
        styles.node,
        styles[data.status],
        selected ? styles.selected : '',
      ].join(' ')}
    >
      <Handle
        type="target"
        position={Position.Left}
        className={styles.handle}
      />
      <div className={styles.header}>
        <span className={[styles.icon, styles[`icon${data.status}`]].join(' ')}>
          <MaterialIcon name={icon} data-testid={`dag-icon-${data.status}`} />
        </span>
        <span className={styles.label} title={data.label}>
          {data.label}
        </span>
        {data.executorKind && (
          <span className={styles.executorTag}>{data.executorKind}</span>
        )}
        <DagNodeExecutorBadge data={data} />
        {data.terminalOutcome && (
          <span className={styles.terminalTag}>{data.terminalOutcome}</span>
        )}
      </div>
      <DagNodeDefinitionMeta data={data} />
      {formatDuration(data.status, data.duration) && (
        <div className={styles.duration}>
          {formatDuration(data.status, data.duration)}
        </div>
      )}
      {data.status === 'not_applicable' && (
        <div className={styles.statusText}>{STATUS_LABEL.not_applicable}</div>
      )}
      <ChipList title="输入" items={data.inputs} variant="in" />
      <ChipList title="输出" items={data.outputs} variant="out" />
      <Handle
        type="source"
        position={Position.Right}
        className={styles.handle}
      />
    </div>
  )
})
