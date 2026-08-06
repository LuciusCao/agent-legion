/**
 * query key 工厂扩展（queryKeys.ts 已达体积预算，后续新 key 统一放这里）。
 * 与 lib/queryKeys.ts 同一约定：相同 key 的多个 useQuery 合并为一次请求。
 */
export const extraQueryKeys = {
  users: () => ['users'] as const,
  // 单个 workspace 记录（AddDialog 与 useWorkspaceDisplayName 共享）。
  workspace: (id: string) => ['workspace', id] as const,
  workspaceMembers: (workspaceId: string) =>
    ['workspaceMembers', workspaceId] as const,
  workerTokens: () => ['workerTokens'] as const,
  tokenUsagePricing: () => ['tokenUsagePricing'] as const,
  // SettingsPage 与 WorkspaceMainPage 经同一 key 共享工作流定义缓存。
  workflowDefinition: (workflowKey: string) =>
    ['workflowDefinition', workflowKey] as const,
  workspaceSettings: (workspaceId: string) =>
    ['workspaceSettings', workspaceId] as const,
  failedNodeRuns: (
    workspaceId: string,
    workflowKey: string | null | undefined
  ) => ['failedNodeRuns', workspaceId, workflowKey ?? null] as const,
  workspaceTokenUsage: (
    workspaceId: string,
    filters: {
      groupBy: string
      nodeKey: string
      model: string
      skillVersion: string
    }
  ) => ['workspaceTokenUsage', workspaceId, filters] as const,
  jobTokenUsage: (jobId: string) => ['jobTokenUsage', jobId] as const,
  // runStatus 进 key：run 状态变化自动重取（对齐原 useAsync deps）。
  runTokenUsage: (jobId: string, runId: number, runStatus: string) =>
    ['runTokenUsage', jobId, runId, runStatus] as const,
  workflowStudioData: (workspaceId: string) =>
    ['workflowStudioData', workspaceId] as const,
  agentDefinitions: () => ['agentDefinitions'] as const,
  agentVersions: (agentId: string) => ['agentVersions', agentId] as const,
}
