import { useQuery } from '@tanstack/react-query'
import { fetchCampaign } from '../api/campaignApi'
import { queryKeys } from '../lib/queryKeys'

/**
 * 单个 campaign 详情（计数/游标/水位轨迹）。未终态（pending/running/
 * paused/failed 前的中间态）时每 5s 轮询；failed 是终态但 error_message
 * 与计数在 flip 时一并落库，无需继续轮询。
 */
const NON_TERMINAL_STATUSES = new Set(['pending', 'running', 'paused'])

export function useCampaign(
  workspaceId: string | undefined,
  campaignId: string | null
) {
  return useQuery({
    queryKey: queryKeys.campaign(workspaceId ?? '', campaignId ?? ''),
    queryFn: () => fetchCampaign(workspaceId as string, campaignId as string),
    enabled: !!workspaceId && campaignId != null,
    refetchInterval: (query) =>
      NON_TERMINAL_STATUSES.has(query.state.data?.campaign.status ?? '')
        ? 5000
        : false,
  })
}
