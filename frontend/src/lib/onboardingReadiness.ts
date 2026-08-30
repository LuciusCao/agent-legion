import type { WorkflowDefinitionRecord } from '../types'
import type { WorkspaceAgentRouteEntry } from '../hooks/useWorkspaceSettingsQuery'

export interface EmptyGuideVisibilityInput {
  filteredJobIds: string[]
  totalJobs: number
  jobsLoading: boolean
  filtersActive: boolean
  /**
   * stats 未到时为 undefined；后端无 published workflow 时是 null（生成
   * 类型标 string，运行时可空），两者都算已 settle。字段名保留
   * workflowKey 是历史口径——调用方传的是 workspace_id（#211 Phase 2：
   * workflow_key 已 deprecated，值恒等）。
   */
  workflowKey: string | null | undefined
  /** active revision 查询在途时为 false（data 仍为 undefined）。 */
  workflowDefinitionLoaded: boolean
}

/**
 * 引导可见 = 无任务、无筛选，且 stats 与 active revision 都已 settle。
 * jobStore 初值 isLoading=false / totalJobs=null（按 0 计），stats 与
 * revision 未到时无法分辨「真空白」与「加载中」——不等 settle 会在有任务
 * 的 workspace 首帧闪现引导，还会误触发引导专属的设置快照请求。
 */
export function shouldShowEmptyGuide(
  input: EmptyGuideVisibilityInput
): boolean {
  return (
    input.filteredJobIds.length === 0 &&
    input.totalJobs === 0 &&
    !input.jobsLoading &&
    !input.filtersActive &&
    input.workflowKey !== undefined &&
    input.workflowDefinitionLoaded
  )
}

export interface OnboardingStepsInput {
  /** workspace id（workflow_key 的恒等替身，#211 Phase 2 deprecated 切换）。 */
  workflowKey: string | undefined
  workflowDefinition: WorkflowDefinitionRecord | null
  agentRoutes: WorkspaceAgentRouteEntry[]
  workspaceId: string | undefined
  goStudio: () => void
  openAddItems: () => void
}

/**
 * 新 workspace 引导的步骤构造与就绪判定（纯函数，便于独立测试）。
 * 「已发布」判定用 active revision 存在性而非 workflow_key 非空——
 * schema v62 起 key 在创建时就与 workspace id 绑定（恒非空），真正的
 * 发布信号是 active revision（workflowDefinition 非空）。provider/model
 * 的解析链与后端 resolve_execution_block 一致：agent 节点 execution.*
 * 为准——active revision 快照里节点 execution 已被 loader 合并了顶层
 * execution 默认，前端直接读节点值即为有效值（workspace agentDefaults
 * 与接入模式勾选已随 schema v64 退役；接入可用性由「已发布 active
 * revision」承载）。
 */
export function buildOnboardingSteps(input: OnboardingStepsInput) {
  const published = !!input.workflowDefinition
  const configured = published && agentNodesReady(input)
  return [
    {
      icon: 'account_tree',
      title: '创建并发布 Workflow',
      description: '在 Studio 中编辑 workflow 草稿，对比并发布第一个版本。',
      unlocked: true,
      completed: published,
      actionLabel: '进入 Studio',
      onAction: input.goStudio,
    },
    {
      icon: 'settings',
      title: '配置 Agent 执行',
      description:
        '为 Agent 节点设置 provider / model（Studio 节点覆盖或 workflow 顶层 execution 默认）。',
      unlocked: published,
      completed: configured,
      actionLabel: '去配置',
      // Settings 已无 provider/model 配置面（schema v64 退役），配置面在
      // Studio 的节点详情 / 顶层 execution。
      onAction: input.goStudio,
    },
    {
      icon: 'add_task',
      title: '添加第一个任务',
      description:
        '按条目类型（material / ref / bundle）添加条目，启动你的第一个任务。',
      unlocked: published && configured,
      actionLabel: '添加条目',
      onAction: input.openAddItems,
    },
  ]
}

function agentNodesReady({
  workflowDefinition,
  workspaceId,
  agentRoutes,
}: OnboardingStepsInput): boolean {
  if (!workflowDefinition || !workspaceId) return false
  // agent 节点 = active revision 中路由到 published Agent 的节点（快照
  // agentRoutes 按 capability 匹配物化而来，与后端 _agent_routes 同源）。
  // 过滤键用 workspace_id（workflow_key 已 deprecated，v62 起恒等，#211）。
  const agentNodeKeys = new Set(
    agentRoutes
      .filter((route) => route.workflow_key === workspaceId)
      .map((route) => route.node_key)
  )
  return (workflowDefinition.nodes ?? []).every((node) => {
    if (!agentNodeKeys.has(node.key)) return true
    return Boolean(node.execution?.provider && node.execution?.model)
  })
}
