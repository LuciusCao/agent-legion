import { useQuery } from '@tanstack/react-query'
import { fetchJobDetail } from '../api'
import { queryKeys } from '../lib/queryKeys'

/**
 * 共享的 job detail 查询。Job 详情页与 artifact hooks 订阅同一 key，
 * 命中同一缓存，不产生额外请求；首次拉取与轮询由页面侧的 useJobDetail
 * 负责，因此这里关闭 refetchOnMount，避免订阅方挂载时对过期缓存补发请求。
 */
export function useJobDetailQuery(jobId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.jobDetail(jobId ?? ''),
    queryFn: () => fetchJobDetail(jobId as string),
    enabled: Boolean(jobId),
    refetchOnMount: false,
  })
}
