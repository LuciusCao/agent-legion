import { memo, useState } from 'react'
import { Handle, Position, NodeProps, type Node } from '@xyflow/react'
import styles from './DagNode.module.css'

export interface DagNodeData extends Record<string, unknown> {
  label: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'stale'
  duration?: number
  executorKind?: 'local' | 'pi' | 'openclaw' | null
  inputs: string[]
  outputs: string[]
}

export type DagNodeType = Node<DagNodeData, 'dagNode'>

const STATUS_ICON: Record<DagNodeData['status'], string> = {
  completed: 'check_circle',
  running: 'hourglass_empty',
  failed: 'error',
  stale: 'warning',
  pending: 'radio_button_unchecked',
}

const CHIP_LIMIT = 3

function ChipList({
  title,
  items,
  variant,
}: {
  title: string
  items: string[]
  variant: 'in' | 'out'
}) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? items : items.slice(0, CHIP_LIMIT)
  const hidden = items.length - visible.length

  if (items.length === 0) return null

  return (
    <div className={styles.chipGroup}>
      <div className={styles.chipTitle}>
        {title}（{items.length}）
      </div>
      <div className={styles.chipRow}>
        {visible.map((item) => (
          <span
            key={item}
            className={[
              styles.chip,
              variant === 'out' ? styles.chipOut : '',
            ].join(' ')}
            title={item}
          >
            {item.length > 18 ? item.slice(0, 17) + '…' : item}
          </span>
        ))}
        {hidden > 0 && !expanded && (
          <button
            className={styles.moreButton}
            onClick={(e) => {
              e.stopPropagation()
              setExpanded(true)
            }}
          >
            +{hidden}
          </button>
        )}
      </div>
    </div>
  )
}

export const DagNode = memo(function DagNode(props: NodeProps<DagNodeType>) {
  const { data, selected } = props
  const icon = STATUS_ICON[data.status]
  const durationText =
    data.status === 'running'
      ? `运行中 ${data.duration ?? 0}s`
      : typeof data.duration === 'number'
        ? `耗时 ${data.duration}s`
        : ''

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
          {icon}
        </span>
        <span className={styles.label} title={data.label}>
          {data.label}
        </span>
        {data.executorKind && (
          <span className={styles.executorTag}>{data.executorKind}</span>
        )}
      </div>
      {durationText && <div className={styles.duration}>{durationText}</div>}
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
