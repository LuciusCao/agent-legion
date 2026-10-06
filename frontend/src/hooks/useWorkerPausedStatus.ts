import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAgentsStore } from '../stores/agentsStore'

/** workspace 调度暂停位的 RQ key（顶栏、引导、排查横幅共享同一缓存）。 */
export function workerPausedStatusKey(workspaceId: string | undefined) {
  return ['workerReadinessStatus', workspaceId] as const
}

/** #961：暂停位的唯一数据源。拉取失败不默认成「已暂停」——调用方必须按
 * isPending / isError 区分「读取中」「状态未知」与真实的暂停/运行。被写入
 * 取代的过时读取（superseded）保留缓存值，不回退到写入之前的快照。 */
export function useWorkerPausedStatus(
  workspaceId: string | undefined,
  enabled = true
) {
  const fetchWorkerStatus = useAgentsStore((s) => s.fetchWorkerStatus)
  const client = useQueryClient()
  return useQuery({
    queryKey: workerPausedStatusKey(workspaceId),
    queryFn: async () => {
      const read = await fetchWorkerStatus(workspaceId!)
      // 响应缺 paused 布尔值时按拉取失败处理（显示「状态未知」），不猜默认值。
      if (typeof read?.paused !== 'boolean') {
        throw new Error('运行状态响应缺少 paused 字段')
      }
      if (!read.superseded) return read.paused
      return (
        client.getQueryData<boolean>(workerPausedStatusKey(workspaceId)) ??
        read.paused
      )
    },
    enabled: !!workspaceId && enabled,
    staleTime: 0,
  })
}
