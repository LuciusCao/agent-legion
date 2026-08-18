import type { DagNodeChangeType } from './DagGraph'
import styles from './DagNodeChangeBadge.module.css'

const CHANGE_LABEL: Record<DagNodeChangeType, string> = {
  added: '新增',
  modified: '已改',
  removed: '已删',
}

/** DAG 节点的未发布变更角标（Studio 草稿对比），无变更时不渲染。 */
export function DagNodeChangeBadge({
  changeType,
}: {
  changeType?: DagNodeChangeType
}) {
  if (!changeType) return null
  return (
    <span
      className={[styles.badge, styles[changeType]].join(' ')}
      data-testid={`dag-change-badge-${changeType}`}
    >
      {CHANGE_LABEL[changeType]}
    </span>
  )
}
