import type { AgentDefaults, WorkflowDefinitionRecord } from '../types'
import type { WorkspaceAgentRouteEntry } from '../hooks/useWorkspaceSettingsQuery'

export interface EmptyGuideVisibilityInput {
  filteredJobIds: string[]
  totalJobs: number
  jobsLoading: boolean
  filtersActive: boolean
  /**
   * stats 未到时为 undefined；后端无 published workflow 时是 null（生成
   * 类型标 string，运行时可空），两者都算已 settle。
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
  workflowKey: string | undefined
  workflowDefinition: WorkflowDefinitionRecord | null
  agentRoutes: WorkspaceAgentRouteEntry[]
  agentDefaults: AgentDefaults | undefined
  intakeModes: string[] | undefined
  workspaceId: string | undefined
  goStudio: () => void
  goSettings: () => void
  openAddItems: () => void
}

/**
 * 新 workspace 引导的步骤构造与就绪判定（纯函数，便于独立测试）。
 * provider/model 的解析链与后端 resolve_execution_block 一致：agent 节点
 * execution.* 覆盖优先，缺失落回 workspace agentDefaults——节点侧已配齐时
 * workspace 默认为空同样是合法可运行状态（AGENTS.md 解析链条目）。接入
 * 模式须至少勾选一个（空列表时 AddItemsDialog 无可选模式）。
 */
export function buildOnboardingSteps(input: OnboardingStepsInput) {
  const published = !!input.workflowKey
  const agentReady = published && agentNodesReady(input)
  const intakeReady = intakeModesReady(input.intakeModes)
  const configured = agentReady && intakeReady
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
      title: '配置 Agent 与接入',
      description:
        '为 Agent 节点设置 provider / model（Studio 节点覆盖或 Settings 默认），并勾选接入模式。',
      unlocked: published,
      completed: configured,
      actionLabel: '去配置',
      onAction: input.goSettings,
    },
    {
      icon: 'add_task',
      title: '添加第一个任务',
      description: '按接入模式添加条目，启动你的第一个任务。',
      unlocked: published && configured,
      actionLabel: '添加条目',
      onAction: input.openAddItems,
    },
  ]
}

function agentNodesReady({
  workflowDefinition,
  workflowKey,
  agentRoutes,
  agentDefaults,
}: OnboardingStepsInput): boolean {
  if (!workflowDefinition || !workflowKey) return false
  // agent 节点 = active revision 中路由到 published Agent 的节点（快照
  // agentRoutes 按 capability 匹配物化而来，与后端 _agent_routes 同源）。
  const agentNodeKeys = new Set(
    agentRoutes
      .filter((route) => route.workflow_key === workflowKey)
      .map((route) => route.node_key)
  )
  return (workflowDefinition.nodes ?? []).every((node) => {
    if (!agentNodeKeys.has(node.key)) return true
    const provider = node.execution?.provider || agentDefaults?.provider || ''
    const model = node.execution?.model || agentDefaults?.model || ''
    return Boolean(provider && model)
  })
}

function intakeModesReady(intakeModes: string[] | undefined): boolean {
  return (intakeModes ?? []).length > 0
}
