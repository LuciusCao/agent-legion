import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import {
  archiveJobBatchOperation,
  listJobBatchOperations,
} from '../../api/jobBatchOperationsListApi'
import { jobBatchOpKeys } from './useJobBatchOps'

/** active 视图轮询间隔与 run-batches 对齐：进行中 3s，空闲 15s。 */
export function useActiveJobBatchOps(workspaceId: string | undefined) {
  return useQuery({
    queryKey: [...jobBatchOpKeys.all(workspaceId ?? ''), 'active'] as const,
    queryFn: () => listJobBatchOperations(workspaceId!, { view: 'active' }),
    enabled: Boolean(workspaceId),
    refetchInterval: (query) =>
      (query.state.data?.active_count ?? 0) > 0 ? 3000 : 15000,
  })
}

const ENDED_PAGE_SIZE = 12

/** ended / archived 瀑布流（keyset cursor）；enabled 由调用方按当前视图给，
 * 与 run-batches 同款（主视图不空发 archived 首页请求）。 */
export function useJobBatchOpWaterfall(
  workspaceId: string | undefined,
  view: 'ended' | 'archived',
  enabled = true
) {
  return useInfiniteQuery({
    queryKey: [...jobBatchOpKeys.all(workspaceId ?? ''), view] as const,
    queryFn: ({ pageParam }) =>
      listJobBatchOperations(workspaceId!, {
        view,
        limit: ENDED_PAGE_SIZE,
        cursor: pageParam,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    enabled: Boolean(workspaceId) && enabled,
  })
}

/** 归档（写后失效全部视图 + 徽标）。 */
export function useJobBatchOpArchive(workspaceId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (opId: string) => archiveJobBatchOperation(workspaceId!, opId),
    onSettled: () =>
      queryClient.invalidateQueries({
        queryKey: jobBatchOpKeys.all(workspaceId ?? ''),
      }),
  })
}
