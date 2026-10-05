import { MaterialIcon } from '../MaterialIcon'
import type { JobNode } from '../../types/jobTypes'
import styles from './JobProgressPanel.module.css'

type HydrationDefer = NonNullable<JobNode['hydration_defer']>

const REASON_LABELS: Record<string, string> = {
  object_missing: '对象已缺失',
  hash_mismatch: '内容校验不符',
  corrupt: '压缩对象损坏',
}

/** #887：等待中节点因输入恢复不全（悬挂清单行）被挡——区分普通排队，
 * 提示重跑哪个生产节点。只在后端确有 defer 公告时渲染。 */
export function JobNodeHydrationDefer({
  defer,
  allNodes,
}: {
  defer: HydrationDefer
  allNodes: JobNode[]
}) {
  const labelOf = (key: string) =>
    allNodes.find((n) => n.node_key === key)?.label || key
  const rerun = defer.rerun_nodes.map(labelOf).join('、')
  const reasons = defer.reasons.map((r) => REASON_LABELS[r] ?? r).join('、')
  return (
    <div
      className={styles.hydrationDefer}
      role="status"
      title={`输入 ${defer.inputs.join('、')}：${reasons}`}
    >
      <MaterialIcon
        name="warning"
        className={styles.toggleIcon}
        sx={{ fontSize: 14 }}
      />
      输入恢复不全，建议重跑 {rerun}
    </div>
  )
}
