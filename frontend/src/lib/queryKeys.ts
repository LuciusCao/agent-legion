import type { OpsMetricsParams } from '../api/metrics'

/**
 * 全局 query key 工厂。相同 key 的多个 useQuery 自动合并为一次请求
 * （如 MonitoringPanel 与 QueueDepthChartSection 的 opsMetrics 轮询）。
 */
export const queryKeys = {
  agentWorkers: () => ['agentWorkers'] as const,
  opsMetrics: (params: OpsMetricsParams) => ['opsMetrics', params] as const,
}
