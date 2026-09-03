/**
 * query key 工厂扩展（queryKeys.ts 已达体积预算，后续新 key 统一放这里）。
 * 与 lib/queryKeys.ts 同一约定：相同 key 的多个 useQuery 合并为一次请求。
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
  failedNodeRuns: (ws: string, workflowKey: string | null | undefined) =>
    ['failedNodeRuns', ws, workflowKey ?? null] as const,
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
  // Studio 编辑器的服务端持久化草稿（GET/PUT workflow-draft）。
  workflowStudioDraft: (workspaceId: string) =>
    ['workflowStudioDraft', workspaceId] as const,
  agentDefinitions: (workspaceId: string) => k('agentDefinitions', workspaceId),
  // Studio DAG/Inspector 共享的 Agent 目录（P-0.5：executors 半区已退役）；
  // 面板发布/归档后失效重取。
  studioAgentCatalog: (workspaceId: string) =>
    ['studioAgentCatalog', workspaceId] as const,
  // Studio 节点详情的技能文件预览；ref 进 key（版本切换重取），Studio 对话
  // turn_end 按首段 'studioSkillDetail' 前缀整体失效（useStudioChat）。
  studioSkillDetail: (skillKey: string, ref: string | null) =>
    ['studioSkillDetail', skillKey, ref] as const,
  // #410：节点检查器 Skill 区块 latest 绑定的实际执行版本回显（最近 run
  // 的 skill_version）。
  // #410：节点检查器 Skill 区块 latest 绑定的实际执行版本回显（最近 run
  // 的 skill_version）。skill 进 key（codex 四轮 P1 on #427）：换绑后绑定
  // key 变化即重新查询，不复用旧 key 的缓存；查询本身也按该列过滤
  // （schema v75 的 node_runs.skill）。运行完成路径按 'nodeRuns' 前缀整体
  // 失效（useWorkspaceEvents 的 job 更新事件，codex 四轮 P2 on #427）。
  nodeRuns: (ws: string, nodeKey: string, skill: string) =>
    ['nodeRuns', ws, nodeKey, skill] as const,
  // Studio 节点运行 Prompt 预览；草稿 YAML 进 key（编辑 debounce 后重取）。
  studioNodePromptPreview: (ws: string, nodeKey: string, yaml: string) =>
    ['studioNodePromptPreview', ws, nodeKey, yaml] as const,
  agentVersions: (workspaceId: string, agentId: string) =>
    ['agentVersions', workspaceId, agentId] as const,
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
  qualityReplayDetail: (workspaceId: string, replayId: string) =>
    ['qualityReplayDetail', workspaceId, replayId] as const,
  // Agent 发起的 workflow 发布请求（#416）：轮询 pending 弹确认对话框。
  studioPublishRequest: (ws: string) => k('studioPublishRequest', ws),
}
