import { memo } from 'react'
import { Handle, Position, NodeProps, type Node } from '@xyflow/react'
import { formatDuration, STATUS_ICON, STATUS_LABEL } from '../dagNodeStatus'
import { ChipList } from './DagNodeChips'
import { MaterialIcon } from '../MaterialIcon'
import { DagNodeChangeBadge } from './DagNodeChangeBadge'
import { DagNodeDefinitionMeta } from './DagNodeDefinitionMeta'
import { DagNodeExecutionBadge } from './DagNodeExecutionBadge'
import type { DagNodeData } from './dagNodeTypes'
import badgeStyles from './DagNodeChangeBadge.module.css'
import styles from './DagNode.module.css'

export type { DagNodeData } from './dagNodeTypes'

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
        data.ghost ? badgeStyles.ghost : '',
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
        <DagNodeExecutionBadge data={data} />
        <DagNodeChangeBadge changeType={data.changeType} />
        {data.executorUnbound && (
          <span
            className={styles.unboundTag}
            title="该节点没有 executor 绑定，调度将失败"
          >
            未绑定
          </span>
        )}
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
