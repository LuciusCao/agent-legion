import type { DagNodeData } from './DagNode'
import badgeStyles from './DagNodeBadges.module.css'

// Short chip label for a worker id: strip any "worker-" prefix, keep 8 chars.
export function shortWorkerId(workerId: string): string {
  return workerId.replace(/^worker[-_]?/i, '').slice(0, 8)
}

export function DagNodeExecutionBadge({ data }: { data: DagNodeData }) {
  const { agentId, workerId, nodeKey } = data
  // #1180：执行徽标只显示真实执行主体（认领后的 worker 短码、agent 节点
  // 身份）。自含 agent 节点的 agent_id 投影即 node key（#933），与卡片
  // 下方「Key · node_key」重复，不作为执行主体展示；executorId 回落分支
  // 已删除（隐含 code 池的池键 'code' 不是执行主体身份）。
  const agentIdentity = agentId && agentId !== nodeKey ? agentId : null
  if (!agentIdentity && !workerId) return null
  const title = [agentIdentity, workerId].filter(Boolean).join(' / ')
  const label = workerId ? shortWorkerId(workerId) : agentIdentity
  return (
    <span
      data-testid="dag-node-execution-badge"
      className={badgeStyles.workerTag}
      title={title}
    >
      {label}
    </span>
  )
}
