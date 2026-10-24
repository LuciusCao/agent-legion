import type { DagNodeStatus } from '../dagNodeStatus'

export type DagNodeChangeType = 'added' | 'modified' | 'removed'

// 节点执行类型（#284 显式 type 词汇的展示层迁移，#1180）：卡片头部类型
// 徽标的取值全集。code = 隐含 code 池节点（EXEC-CODE-POOL-001）；agent =
// 自含执行档案的 agent 节点（EXEC-AGENT-PROFILE-001）。start/approval 不
// dispatch，没有执行类型（值为 null，不渲染类型徽标）。
export type NodeExecutionType = 'code' | 'agent'

/** 类型徽标文案：agent 节点声明了 execution.runtime 时附 runtime
 * （agent·velites——旧「Pi Agent」标签真正想表达的语义，#1180）。 */
export function nodeExecutionTypeLabel(
  data: Pick<DagNodeData, 'executionType' | 'executionRuntime'>
): string | null {
  if (!data.executionType) return null
  if (data.executionType === 'agent' && data.executionRuntime) {
    return `agent·${data.executionRuntime}`
  }
  return data.executionType
}

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
  executionType?: NodeExecutionType | null
  /** agent 节点声明的 execution.runtime（如 velites）；仅 Studio 侧有记录
   * 侧数据源，job 详情契约不带 runtime，恒为 null。 */
  executionRuntime?: string | null
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
