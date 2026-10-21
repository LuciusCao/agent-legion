import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import {
  archiveRunBatch,
  cancelRunBatch,
  listRunBatches,
  pauseRunBatch,
  resumeRunBatch,
  type RunBatchView,
} from '../../api/runBatchesApi'

/**
 * 任务中心数据 hooks（#1157 phase3）。查询 key 集中在本地（feature 内聚，
 * 不进全局 queryKeysExtra）；active 视图同时喂 AppBar 徽标——同一 key
 * 共享缓存，徽标轮询即任务中心轮询。
 */
export const runBatchKeys = {
  all: (workspaceId: string) => ['run-batches', workspaceId] as const,
  view: (workspaceId: string, view: RunBatchView) =>
    ['run-batches', workspaceId, view] as const,
}

/** active 视图轮询间隔：进行中 3s，空闲 15s。 */
export function useActiveRunBatches(workspaceId: string | undefined) {
  return useQuery({
    queryKey: runBatchKeys.view(workspaceId ?? '', 'active'),
    queryFn: () => listRunBatches(workspaceId!, { view: 'active' }),
    enabled: Boolean(workspaceId),
    refetchInterval: (query) =>
      (query.state.data?.active_count ?? 0) > 0 ? 3000 : 15000,
  })
}

const ENDED_PAGE_SIZE = 12

/** ended / archived 瀑布流（keyset cursor，「加载更早的记录」）。 */
export function useRunBatchWaterfall(
  workspaceId: string | undefined,
  view: 'ended' | 'archived'
) {
  return useInfiniteQuery({
    queryKey: runBatchKeys.view(workspaceId ?? '', view),
    queryFn: ({ pageParam }) =>
      listRunBatches(workspaceId!, {
        view,
        limit: ENDED_PAGE_SIZE,
        cursor: pageParam,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    enabled: Boolean(workspaceId),
  })
}

/** 卡片操作（暂停/继续/取消/归档）：写后失效全部三视图 + 徽标。 */
export function useRunBatchActions(workspaceId: string | undefined) {
  const queryClient = useQueryClient()
  const onSettled = () =>
    queryClient.invalidateQueries({
      queryKey: runBatchKeys.all(workspaceId ?? ''),
    })
  const pause = useMutation({
    mutationFn: (runId: string) => pauseRunBatch(workspaceId!, runId),
    onSettled,
  })
  const resume = useMutation({
    mutationFn: (runId: string) => resumeRunBatch(workspaceId!, runId),
    onSettled,
  })
  const cancel = useMutation({
    mutationFn: (runId: string) => cancelRunBatch(workspaceId!, runId),
    onSettled,
  })
  const archive = useMutation({
    mutationFn: (runId: string) => archiveRunBatch(workspaceId!, runId),
    onSettled,
  })
  return { pause, resume, cancel, archive }
}
