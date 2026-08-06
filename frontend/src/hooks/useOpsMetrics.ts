import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { fetchOpsMetrics, type OpsMetricsParams } from '../api/metrics'
import { queryKeys } from '../lib/queryKeys'

/**
 * 运维监控指标查询。同一参数组合共享 queryKey，多调用点（监控面板主数据与
 * 队列深度图）自动合并为一次请求；切换过滤条件时保留上一批数据，避免
 * 摘要卡/图表闪空（同原 useAsync 行为）。
 */
export function useOpsMetrics(
  params: OpsMetricsParams,
  refetchInterval: number
) {
  return useQuery({
    queryKey: queryKeys.opsMetrics(params),
    queryFn: () => fetchOpsMetrics(params),
    refetchInterval,
    placeholderData: keepPreviousData,
  })
}
