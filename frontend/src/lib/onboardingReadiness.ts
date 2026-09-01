import type { WorkflowDefinitionRecord } from '../types'

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
  workflowDefinition: WorkflowDefinitionRecord | null
  goStudio: () => void
  openAddItems: () => void
}

/**
 * 新 workspace 引导的步骤构造与就绪判定（纯函数，便于独立测试）。
 * 「已发布」判定用 active revision 存在性而非 workflow_key 非空——
 * schema v62 起 key 在创建时就与 workspace id 绑定（恒非空），真正的
 * 发布信号是 active revision（workflowDefinition 非空）。
 * #333：原第 2 步「配置 Agent 执行」已移除——v63/v64 退役 workspace
 * agentDefaults 与接入模式后该步只剩 execution 检查，对纯 code
 * workflow 恒为空集、随第 1 步自动打勾；其真实缺口（agent 节点缺
 * provider/model 时首个 job 在 dispatch 才 fail-fast）改由 Studio
 * 画布的实时警报承载（canvas/workflowStudioExecutionWarnings）。
 */
export function buildOnboardingSteps(input: OnboardingStepsInput) {
  const published = !!input.workflowDefinition
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
      icon: 'add_task',
      title: '添加第一个任务',
      description:
        '按条目类型（material / ref / bundle）添加条目，启动你的第一个任务。',
      unlocked: published,
      actionLabel: '添加条目',
      onAction: input.openAddItems,
    },
  ]
}
