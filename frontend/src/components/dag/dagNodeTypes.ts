import type { ExecutorKind } from '../../types/jobTypes'
import type { DagNodeStatus } from '../dagNodeStatus'

export type DagNodeChangeType = 'added' | 'modified' | 'removed'

// 拓扑/类型徽标联合（#392 Phase 3）：approval 为审批门专属徽标。
export type TopologyBadge =
  | 'start'
  | 'approval'
  | 'entry'
  | 'branch'
  | 'terminal'

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
  executorUnbound?: boolean
  /** #333：agent 节点有效 execution 缺 provider/model 时的警告文案（仅
   * Studio 画布注入；缺失即 undefined，节点不显示警告徽标）。 */
  executionWarning?: string
  topologyBadges?: TopologyBadge[]
  terminalOutcome?: string
  inputs: string[]
  outputs: string[]
  changeType?: DagNodeChangeType
  ghost?: boolean
  // active/dimmed（#276）：hover/选中时写入节点 data 的高亮态（目标节点
  // active=蓝色轮廓，非同链路 dimmed=置灰）。放 data 而非 node.style，
  // 未受影响节点的 data 引用保持稳定，React.memo 才能在 hover 时不重渲染
  // 它们（完整论证见 dagNodeMemo.ts 头注释）。
  active?: boolean
  dimmed?: boolean
}
