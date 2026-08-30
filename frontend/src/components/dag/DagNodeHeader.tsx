import { MaterialIcon } from '../MaterialIcon'
import { DagNodeChangeBadge } from './DagNodeChangeBadge'
import { DagNodeExecutionBadge } from './DagNodeExecutionBadge'
import type { DagNodeData } from './dagNodeTypes'
import styles from './DagNode.module.css'
import { STATUS_ICON } from '../dagNodeStatus'

/**
 * DagNode 头部行（#276 预算拆分）：状态图标、标签、executor/agent 徽标与
 * 未绑定/终态标记。渲染输入只有 data 的展示字段（memo 由外层 DagNode 兜底，
 * 这里不重复 memo——它随 DagNode 一起执行）。
 */
export function DagNodeHeader({ data }: { data: DagNodeData }) {
  const icon = STATUS_ICON[data.status]
  return (
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
  )
}
