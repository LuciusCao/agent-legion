import { api } from './core'
import type { components } from '../generated/api'

export type QualitySampleBatch = components['schemas']['QualitySampleBatch']
export type QualitySampleBatchCreateRequest =
  components['schemas']['QualitySampleBatchCreateRequest']
export type QualitySampleBatchCreateResponse =
  components['schemas']['QualitySampleBatchCreateResponse']
export type QualitySampleBatchListResponse =
  components['schemas']['QualitySampleBatchListResponse']
export type QualitySampleBatchDetailResponse =
  components['schemas']['QualitySampleBatchDetailResponse']
export type QualitySampleItem = components['schemas']['QualitySampleItem']
export type QualitySampleItemDetailResponse =
  components['schemas']['QualitySampleItemDetailResponse']
export type QualityBatchStatsResponse =
  components['schemas']['QualityBatchStatsResponse']
export type QualityStatsGroup = components['schemas']['QualityStatsGroup']
export type QualityLabel = components['schemas']['QualityLabel']
export type QualityLabelCreateRequest =
  components['schemas']['QualityLabelCreateRequest']
export type QualityLabelResponse = components['schemas']['QualityLabelResponse']
export type QualityArtifactContent =
  components['schemas']['QualityArtifactContent']
export type QualityReplay = components['schemas']['QualityReplay']
export type QualityReplayCreateRequest =
  components['schemas']['QualityReplayCreateRequest']
export type QualityReplayResponse =
  components['schemas']['QualityReplayResponse']
export type QualityReplayListResponse =
  components['schemas']['QualityReplayListResponse']
export type QualityReplayDetailResponse =
  components['schemas']['QualityReplayDetailResponse']

/** 打标 reason 受控词表（与后端一致）。 */
export const QUALITY_REASON_CODES = [
  'fact_error',
  'answer_leak',
  'inconsistent_answer',
  'non_conceptual_basis',
  'format_violation',
  'other',
] as const

const base = (workspaceId: string) =>
  `/api/workspaces/${encodeURIComponent(workspaceId)}/quality`

export async function fetchSampleBatches(
  workspaceId: string
): Promise<QualitySampleBatchListResponse> {
  return api<QualitySampleBatchListResponse>(
    `${base(workspaceId)}/sample-batches`
  )
}

export async function createSampleBatch(
  workspaceId: string,
  body: QualitySampleBatchCreateRequest
): Promise<QualitySampleBatchCreateResponse> {
  return api<QualitySampleBatchCreateResponse>(
    `${base(workspaceId)}/sample-batches`,
    { method: 'POST', body: JSON.stringify(body) }
  )
}

export async function fetchSampleBatchDetail(
  workspaceId: string,
  batchId: string,
  params?: { limit?: number; offset?: number }
): Promise<QualitySampleBatchDetailResponse> {
  const query = new URLSearchParams()
  if (params?.limit != null) query.set('limit', String(params.limit))
  if (params?.offset != null) query.set('offset', String(params.offset))
  const suffix = query.toString()
  return api<QualitySampleBatchDetailResponse>(
    `${base(workspaceId)}/sample-batches/${encodeURIComponent(batchId)}${suffix ? `?${suffix}` : ''}`
  )
}

export async function fetchSampleBatchStats(
  workspaceId: string,
  batchId: string
): Promise<QualityBatchStatsResponse> {
  return api<QualityBatchStatsResponse>(
    `${base(workspaceId)}/sample-batches/${encodeURIComponent(batchId)}/stats`
  )
}

export async function fetchSampleItemDetail(
  workspaceId: string,
  itemId: string
): Promise<QualitySampleItemDetailResponse> {
  return api<QualitySampleItemDetailResponse>(
    `${base(workspaceId)}/sample-items/${encodeURIComponent(itemId)}`
  )
}

export async function addSampleItemLabel(
  workspaceId: string,
  itemId: string,
  body: QualityLabelCreateRequest
): Promise<QualityLabelResponse> {
  return api<QualityLabelResponse>(
    `${base(workspaceId)}/sample-items/${encodeURIComponent(itemId)}/labels`,
    { method: 'POST', body: JSON.stringify(body) }
  )
}

export async function fetchReplays(
  workspaceId: string,
  itemId: string
): Promise<QualityReplayListResponse> {
  return api<QualityReplayListResponse>(
    `${base(workspaceId)}/sample-items/${encodeURIComponent(itemId)}/replays`
  )
}

export async function createReplay(
  workspaceId: string,
  itemId: string,
  body: QualityReplayCreateRequest
): Promise<QualityReplayResponse> {
  return api<QualityReplayResponse>(
    `${base(workspaceId)}/sample-items/${encodeURIComponent(itemId)}/replays`,
    { method: 'POST', body: JSON.stringify(body) }
  )
}

export async function fetchReplayDetail(
  workspaceId: string,
  replayId: string
): Promise<QualityReplayDetailResponse> {
  return api<QualityReplayDetailResponse>(
    `${base(workspaceId)}/replays/${encodeURIComponent(replayId)}`
  )
}
