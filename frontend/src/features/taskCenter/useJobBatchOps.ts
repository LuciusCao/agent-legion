import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import {
  getJobBatchOperation,
  listJobBatchOperationResults,
} from '../../api/jobBatchOperationsApi'

/**
 * 批量操作（#946）轮询 hooks。查询 key 集中在本地（feature 内聚）。
 * 列表/归档视图的发现面由 M61 的列表端点落地后接入；本模块先提供
 * 单 op 轮询与逐 job 结果瀑布流。
 */
export const jobBatchOpKeys = {
  all: (workspaceId: string) => ['job-batch-ops', workspaceId] as const,
  op: (workspaceId: string, opId: string) =>
    ['job-batch-ops', workspaceId, 'op', opId] as const,
  results: (workspaceId: string, opId: string) =>
    ['job-batch-ops', workspaceId, 'results', opId] as const,
}

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])

/** 单 op 轮询：非终态 3s 一拍，终态自停。 */
export function useJobBatchOperation(
  workspaceId: string | undefined,
  opId: string | undefined,
  enabled = true
) {
  return useQuery({
    queryKey: jobBatchOpKeys.op(workspaceId ?? '', opId ?? ''),
    queryFn: () => getJobBatchOperation(workspaceId!, opId!),
    enabled: Boolean(workspaceId) && Boolean(opId) && enabled,
    refetchInterval: (query) =>
      query.state.data && TERMINAL_STATUSES.has(query.state.data.status)
        ? false
        : 3000,
  })
}

const RESULTS_PAGE_SIZE = 100

/** 逐 job 结果瀑布流（keyset cursor）；enabled 由调用方按 dialog 开合给。 */
export function useJobBatchOpResults(
  workspaceId: string | undefined,
  opId: string | undefined,
  enabled = false
) {
  return useInfiniteQuery({
    queryKey: jobBatchOpKeys.results(workspaceId ?? '', opId ?? ''),
    queryFn: ({ pageParam }) =>
      listJobBatchOperationResults(workspaceId!, opId!, {
        limit: RESULTS_PAGE_SIZE,
        cursor: pageParam,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? null,
    enabled: Boolean(workspaceId) && Boolean(opId) && enabled,
  })
}
