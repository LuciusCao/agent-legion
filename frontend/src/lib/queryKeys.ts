import type { OpsMetricsParams } from '../api/metrics'

/**
 * 全局 query key 工厂。相同 key 的多个 useQuery 自动合并为一次请求
 * （如 MonitoringPanel 与 QueueDepthChartSection 的 opsMetrics 轮询）。
 */
export const queryKeys = {
  agentWorkers: () => ['agentWorkers'] as const,
  opsMetrics: (params: OpsMetricsParams) => ['opsMetrics', params] as const,
  workspaces: () => ['workspaces'] as const,
  workspaceStats: (id: string) => ['workspaceStats', id] as const,
  jobDetail: (jobId: string) => ['jobDetail', jobId] as const,
  // version 反映产出该 artifact 的节点状态；版本变 → 新 key → 自动重取，
  // 替代旧的 refreshKey props 管道。
  jobArtifact: (jobId: string, name: string, version: string) =>
    ['jobArtifact', jobId, name, version] as const,
  // 通用预览面板的原始文本读取。与 jobArtifact 分开命名空间：结构化
  // hook（如 useJobQuestion）对同名 artifact 返回归一化对象，queryFn
  // 形状不同，共 key 会让后挂载方读到错误形状（#11）。
  jobArtifactText: (jobId: string, name: string, version: string) =>
    ['jobArtifactText', jobId, name, version] as const,
  studioChatAgents: (workspaceId: string) =>
    ['studio-chat-agents', workspaceId] as const,
  studioChatSessions: (workspaceId: string) =>
    ['studio-chat-sessions', workspaceId] as const,
}
