import { useQuery } from '@tanstack/react-query'
import { fetchCampaigns } from '../api/campaignApi'
import { queryKeys } from '../lib/queryKeys'

/**
 * workspace 的 campaign 列表（最新在前，服务端排序）。存在活跃
 * （pending/running/paused）campaign 时每 5s 轮询刷新计数，全部终态后
 * 停表——campaign 行即全部进度（设计 §3.2：v1 用轮询 GET）。
 */
const ACTIVE_STATUSES = new Set(['pending', 'running', 'paused'])

export function useCampaigns(workspaceId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.campaigns(workspaceId ?? ''),
    queryFn: () => fetchCampaigns(workspaceId as string),
    enabled: !!workspaceId,
    refetchInterval: (query) =>
      (query.state.data?.campaigns ?? []).some((campaign) =>
        ACTIVE_STATUSES.has(campaign.status)
      )
        ? 5000
        : false,
  })
}
