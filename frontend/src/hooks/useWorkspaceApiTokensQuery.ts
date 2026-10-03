import { useQuery } from '@tanstack/react-query'
import { listWorkspaceApiTokens } from '../api'
import { extraQueryKeys } from '../lib/queryKeysExtra'

/**
 * Workspace API token 列表 + 生效限流参数（#626 / #738 / #870）。
 * 「外部对接」section 的 token 面板与接入信息卡共用同一 query key，
 * react-query 去重为一次请求；签发 / 吊销后按该 key 失效即两处同步刷新。
 */
export function useWorkspaceApiTokensQuery(workspaceId: string) {
  return useQuery({
    queryKey: extraQueryKeys.workspaceApiTokens(workspaceId),
    queryFn: () => listWorkspaceApiTokens(workspaceId),
    enabled: workspaceId !== '',
  })
}
