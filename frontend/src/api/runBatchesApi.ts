import { api } from './core'
import type { components } from '../generated/api'
import type { RunCreateRequest } from '../types'

type ApiSchemas = components['schemas']

// 任务中心（#1157 phase3）的 transport 类型，全部从生成的 API 契约派生
// （AGENTS.md：禁止手写 transport types）。
export type RunBatchListItem = ApiSchemas['RunBatchListItem']
export type RunBatchListResponse = ApiSchemas['RunBatchListResponse']
export type RunBatchArchiveResponse = ApiSchemas['RunBatchArchiveResponse']
export type RunAcceptedResponse = ApiSchemas['RunAcceptedResponse']
export type RunBatchView = 'active' | 'ended' | 'archived'

/** 任务中心三视图列表；ended/archived 走 keyset cursor 瀑布流。 */
export async function listRunBatches(
  workspaceId: string,
  options: { view: RunBatchView; limit?: number; cursor?: string | null }
): Promise<RunBatchListResponse> {
  const params = new URLSearchParams({ view: options.view })
  if (options.limit != null) params.set('limit', String(options.limit))
  if (options.cursor) params.set('cursor', options.cursor)
  return api<RunBatchListResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/run-batches?${params}`
  )
}

/** 手动归档终态批量任务；非终态/重复归档由服务端 409。 */
export async function archiveRunBatch(
  workspaceId: string,
  runId: string
): Promise<RunBatchArchiveResponse> {
  return api<RunBatchArchiveResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/run-batches/${encodeURIComponent(runId)}/archive`,
    { method: 'POST' }
  )
}

/**
 * #1157 phase3：大批量提交的异步受理（202 + batch 句柄）。秒级返回，无需
 * 写超时豁免；进度经任务中心轮询 GET /run-batches。放在本模块而非
 * materialsApi（后者贴着体积预算带上沿；异步受理与 batch 句柄同族）。
 */
export async function createRunAsync(
  workspaceId: string,
  request: RunCreateRequest
): Promise<RunAcceptedResponse> {
  return api<RunAcceptedResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/runs`,
    {
      method: 'POST',
      body: JSON.stringify({ ...request, mode: 'async' }),
    }
  )
}

type RunBatchResponse = ApiSchemas['RunBatchResponse']

async function controlRunBatch(
  workspaceId: string,
  runId: string,
  action: 'pause' | 'resume' | 'cancel'
): Promise<RunBatchResponse> {
  return api<RunBatchResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/runs/${encodeURIComponent(runId)}/${action}`,
    { method: 'POST' }
  )
}

/** #1117 投喂控制面：只停/续/止「继续建 job」，已建 job 不动。 */
export const pauseRunBatch = (workspaceId: string, runId: string) =>
  controlRunBatch(workspaceId, runId, 'pause')
export const resumeRunBatch = (workspaceId: string, runId: string) =>
  controlRunBatch(workspaceId, runId, 'resume')
export const cancelRunBatch = (workspaceId: string, runId: string) =>
  controlRunBatch(workspaceId, runId, 'cancel')
