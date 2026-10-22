import { api } from './core'
import type { components } from '../generated/api'
import type { JobBatchOpView } from './jobBatchOperationsApi'

type ApiSchemas = components['schemas']

// 批量操作（#946）任务中心列表与归档的 transport 类型（M61），全部从生成
// 的 API 契约派生（AGENTS.md：禁止手写 transport types）。
export type JobBatchOperationListItem = ApiSchemas['JobBatchOperationListItem']
export type JobBatchOperationListResponse =
  ApiSchemas['JobBatchOperationListResponse']
export type JobBatchOperationArchiveResponse =
  ApiSchemas['JobBatchOperationArchiveResponse']

/** 任务中心三视图列表（M61；与 run-batches 列表同形）：active 全量不分页，
 * ended/archived 走 keyset cursor 瀑布流；active_count 为徽标口径。 */
export async function listJobBatchOperations(
  workspaceId: string,
  options: { view: JobBatchOpView; limit?: number; cursor?: string | null }
): Promise<JobBatchOperationListResponse> {
  const params = new URLSearchParams({ view: options.view })
  if (options.limit != null) params.set('limit', String(options.limit))
  if (options.cursor) params.set('cursor', options.cursor)
  return api<JobBatchOperationListResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/job-batch-operations?${params}`
  )
}

/** 手动归档终态批量操作（幂等）；非终态由服务端 409。 */
export async function archiveJobBatchOperation(
  workspaceId: string,
  opId: string
): Promise<JobBatchOperationArchiveResponse> {
  return api<JobBatchOperationArchiveResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/job-batch-operations/${encodeURIComponent(opId)}/archive`,
    { method: 'POST' }
  )
}
