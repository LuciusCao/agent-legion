import { memo } from 'react'
import { Handle, Position, NodeProps, type Node } from '@xyflow/react'
import { formatDuration, STATUS_LABEL } from '../dagNodeStatus'
import { ChipList } from './DagNodeChips'
import { DagNodeDefinitionMeta } from './DagNodeDefinitionMeta'
import type { DagNodeData } from './dagNodeTypes'
import { DagNodeHeader } from './DagNodeHeader'
import badgeStyles from './DagNodeChangeBadge.module.css'
import { dagNodePropsEqual } from './dagNodeMemo'
import styles from './DagNode.module.css'

export type { DagNodeData } from './dagNodeTypes'

export type DagNodeType = Node<DagNodeData, 'dagNode'>

export const DagNode = memo(
  function DagNode(props: NodeProps<DagNodeType>) {
    const { data, selected } = props

    return (
      <div
        data-testid="dag-node"
        data-status={data.status}
        className={[
          styles.node,
          styles[data.status],
          selected ? styles.selected : '',
          data.active ? styles.active : '',
          data.ghost ? badgeStyles.ghost : '',
        ].join(' ')}
        // #276：hover/选中的置灰态从 node.style 下沉到 data.dimmed，
        // 只有高亮态翻转的节点会拿到新 data 引用并重渲染。
        style={data.dimmed ? { opacity: 0.45 } : undefined}
      >
        <Handle
          type="target"
          position={Position.Left}
          className={styles.handle}
        />
        <DagNodeHeader data={data} />
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
  },
  // #276 memo 比较函数抽到 dagNodeMemo.ts（预算回落）——语义论证见该文件头注释。
  dagNodePropsEqual
)
