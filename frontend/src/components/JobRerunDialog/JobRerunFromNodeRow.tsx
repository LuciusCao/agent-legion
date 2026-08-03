import { Chip } from '@mui/material'
import type { FailureCategoryState } from './useFailureCategories'
import styles from './JobRerunDialog.module.css'

export type RerunFromNodeOption = {
  key: string
  label?: string | null
}

/**
 * 失败类型模式下的可选起始节点：缺省"自动"（按类别策略推导起点），
 * 选择具体节点后从该节点开始重跑（仅作用于失败节点位于其下游的任务）。
 * "全部失败"沿用逐任务失败节点语义，不提供起始节点选择。
 */
export function JobRerunFromNodeRow({
  nodes,
  failure,
}: {
  nodes: RerunFromNodeOption[]
  failure: FailureCategoryState
}) {
  const disabled = failure.selection === 'all'
  return (
    <div
      className={styles.categoryRow}
      title={disabled ? '选择具体失败类型后可指定起始节点' : undefined}
    >
      <Chip
        data-testid="rerun-from-node-auto"
        label="起始节点：自动"
        size="small"
        variant={failure.fromNodeKey == null ? 'filled' : 'outlined'}
        disabled={disabled}
        onClick={() => failure.setFromNodeKey(null)}
      />
      {nodes.map((node) => (
        <Chip
          key={node.key}
          data-testid={`rerun-from-node-${node.key}`}
          label={node.label || node.key}
          size="small"
          variant={failure.fromNodeKey === node.key ? 'filled' : 'outlined'}
          disabled={disabled}
          onClick={() => failure.setFromNodeKey(node.key)}
        />
      ))}
    </div>
  )
}
