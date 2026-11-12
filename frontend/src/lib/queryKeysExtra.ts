import { studioQueryKeys } from './queryKeysStudio'

/**
 * query key 工厂扩展。与 lib/queryKeys.ts 同一约定：相同 key 的多个
 * useQuery 合并为一次请求；Studio 域由 queryKeysStudio 聚合到同一出口。
 */
const k = (name: string, id: string) => [name, id] as const

export const extraQueryKeys = {
  users: () => ['users'] as const,
  // 单个 workspace 记录（AddItemsDialog 与 useWorkspaceDisplayName 共享）。
  workspace: (id: string) => k('workspace', id),
  workspaceMembers: (workspaceId: string) =>
    ['workspaceMembers', workspaceId] as const,
  // 添加条目面板「已有材料」tab 的材料列表。
  workspaceMaterials: (workspaceId: string) =>
    ['workspaceMaterials', workspaceId] as const,
  workerTokens: () => ['workerTokens'] as const,
  // workspace 签发的 API intake token 列表（#626，按 workspace 作用域）。
  workspaceApiTokens: (workspaceId: string) =>
    ['workspaceApiTokens', workspaceId] as const,
  // 部署级入口元数据；专用接口不含 Worker 清单，多个入口共享请求。
  workerConsole: () => ['workerConsole'] as const,
  // workspace 视角的 worker 列表（按 scoped token 注册过滤，issue #35）。
  workspaceWorkers: (workspaceId: string) =>
    ['workspaceWorkers', workspaceId] as const,
  // 在线 Worker 上报的 (runtime, provider, model) 聚合，Studio 节点执行
  // 配置 datalist 数据源。
  workspaceRuntimeModels: (workspaceId: string) =>
    ['workspaceRuntimeModels', workspaceId] as const,
  tokenUsagePricing: () => ['tokenUsagePricing'] as const,
  instanceSettings: () => ['instanceSettings'] as const,
  // SkillSelector datalist 的候选目录（<skills_root>/<workspaceId>/ 下目录名，#327）。
  skillDirectories: (workspaceId: string) => k('skillDirectories', workspaceId),
  studioAgents: () => ['studioAgents'] as const,
  connections: () => ['connections'] as const,
  // key-only 连接 key 列表（任意登录用户可读，#419）：ConnectionsSection
  // 的增删改成功后一并 invalidate（见 applyMutation）。
  connectionKeys: () => ['connectionKeys'] as const,
  // 全局设置「基础设施连接」只读展示（#335）。
  infraConnections: () => ['infraConnections'] as const,
  connectionTypes: () => ['connectionTypes'] as const,
  // SettingsPage 与 WorkspaceMainPage 经同一 key 共享工作流定义缓存。
  workflowDefinition: (key: string) => k('workflowDefinition', key),
  workspaceSettings: (ws: string) => k('workspaceSettings', ws),
  failedNodeRuns: (ws: string) => ['failedNodeRuns', ws] as const,
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
  qualityBatches: (workspaceId: string) =>
    ['qualityBatches', workspaceId] as const,
  qualityBatchDetail: (workspaceId: string, batchId: string) =>
    ['qualityBatchDetail', workspaceId, batchId] as const,
  qualityBatchStats: (workspaceId: string, batchId: string) =>
    ['qualityBatchStats', workspaceId, batchId] as const,
  qualityItemDetail: (workspaceId: string, itemId: string) =>
    ['qualityItemDetail', workspaceId, itemId] as const,
  qualityReplays: (workspaceId: string, itemId: string) =>
    ['qualityReplays', workspaceId, itemId] as const,
  qualityReplayProfiles: (workspaceId: string, itemId: string) =>
    ['qualityReplayProfiles', workspaceId, itemId] as const,
  qualityReplayDetail: (workspaceId: string, replayId: string) =>
    ['qualityReplayDetail', workspaceId, replayId] as const,
  ...studioQueryKeys,
}
