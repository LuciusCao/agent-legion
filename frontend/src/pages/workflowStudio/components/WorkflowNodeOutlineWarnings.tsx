import type { TopologyOrder } from '../workflowStudioTopology'
import styles from '../WorkflowNodeOutlineWarnings.module.css'

type Props = {
  topology: TopologyOrder
}

export function WorkflowNodeOutlineWarnings({ topology }: Props) {
  if (!topology.cyclic && topology.disconnected.length === 0) return null
  return (
    <p className={styles.warning}>
      {topology.cyclic && '检测到环或循环依赖，'}
      {topology.disconnected.length > 0 &&
        `${topology.disconnected.length} 个节点未连接到主流程`}
    </p>
  )
}
