import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  addSampleItemLabel,
  createReplay,
  createSampleBatch,
  fetchReplayDetail,
  fetchReplays,
  fetchSampleBatchDetail,
  fetchSampleBatchStats,
  fetchSampleBatches,
  fetchSampleItemDetail,
  type QualityLabelCreateRequest,
  type QualityReplayCreateRequest,
  type QualitySampleBatchCreateRequest,
} from '../api/qualityApi'
import { extraQueryKeys } from '../lib/queryKeysExtra'

const REPLAY_ACTIVE_STATUSES = new Set(['pending', 'running'])

function replayActive(status: string | undefined): boolean {
  return status != null && REPLAY_ACTIVE_STATUSES.has(status)
}

export function useQualityBatches(workspaceId: string) {
  return useQuery({
    queryKey: extraQueryKeys.qualityBatches(workspaceId),
    queryFn: () => fetchSampleBatches(workspaceId),
  })
}

export function useQualityBatchDetail(
  workspaceId: string,
  batchId: string | null
) {
  return useQuery({
    queryKey: extraQueryKeys.qualityBatchDetail(workspaceId, batchId ?? ''),
    queryFn: () => fetchSampleBatchDetail(workspaceId, batchId as string),
    enabled: batchId != null,
  })
}

export function useQualityBatchStats(
  workspaceId: string,
  batchId: string | null
) {
  return useQuery({
    queryKey: extraQueryKeys.qualityBatchStats(workspaceId, batchId ?? ''),
    queryFn: () => fetchSampleBatchStats(workspaceId, batchId as string),
    enabled: batchId != null,
  })
}

export function useQualityItemDetail(
  workspaceId: string,
  itemId: string | null
) {
  return useQuery({
    queryKey: extraQueryKeys.qualityItemDetail(workspaceId, itemId ?? ''),
    queryFn: () => fetchSampleItemDetail(workspaceId, itemId as string),
    enabled: itemId != null,
  })
}

export function useCreateSampleBatch(workspaceId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: QualitySampleBatchCreateRequest) =>
      createSampleBatch(workspaceId, body),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: extraQueryKeys.qualityBatches(workspaceId),
      }),
  })
}

export function useAddSampleItemLabel(workspaceId: string, itemId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: QualityLabelCreateRequest) =>
      addSampleItemLabel(workspaceId, itemId, body),
    onSuccess: (_data, body) => {
      queryClient.invalidateQueries({
        queryKey: extraQueryKeys.qualityItemDetail(workspaceId, itemId),
      })
      // 打标影响批次列表的 current_label 与统计聚合，按前缀整体失效。
      queryClient.invalidateQueries({
        queryKey: ['qualityBatchDetail', workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ['qualityBatchStats', workspaceId],
      })
      if (body.replay_id) {
        queryClient.invalidateQueries({
          queryKey: extraQueryKeys.qualityReplayDetail(
            workspaceId,
            body.replay_id
          ),
        })
      }
    },
  })
}

/** item 的 replay 列表；存在 pending/running replay 时每 3s 轮询。 */
export function useQualityReplays(workspaceId: string, itemId: string | null) {
  return useQuery({
    queryKey: extraQueryKeys.qualityReplays(workspaceId, itemId ?? ''),
    queryFn: () => fetchReplays(workspaceId, itemId as string),
    enabled: itemId != null,
    refetchInterval: (query) =>
      (query.state.data?.replays ?? []).some((r) => replayActive(r.status))
        ? 3000
        : false,
  })
}

/** replay 详情（新产物 + 冻结输入 + 标签）；进行中时每 3s 轮询。 */
export function useQualityReplayDetail(
  workspaceId: string,
  replayId: string | null
) {
  return useQuery({
    queryKey: extraQueryKeys.qualityReplayDetail(workspaceId, replayId ?? ''),
    queryFn: () => fetchReplayDetail(workspaceId, replayId as string),
    enabled: replayId != null,
    refetchInterval: (query) =>
      replayActive(query.state.data?.replay.status) ? 3000 : false,
  })
}

export function useCreateReplay(workspaceId: string, itemId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: QualityReplayCreateRequest) =>
      createReplay(workspaceId, itemId, body),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: extraQueryKeys.qualityReplays(workspaceId, itemId),
      }),
  })
}
