import { api } from './core'
import type { components } from '../generated/api'

type ApiSchemas = components['schemas']

// 批量操作异步化（#946）受理句柄与轮询面的 transport 类型，全部从生成的
// API 契约派生（AGENTS.md：禁止手写 transport types）。
export type JobBatchOperation = ApiSchemas['JobBatchOperationResponse']
export type JobBatchOperationAccepted =
  ApiSchemas['JobBatchOperationAcceptedResponse']
export type JobBatchOperationKind = JobBatchOperation['kind']
export type JobBatchOperationResultsPage =
  ApiSchemas['JobBatchOperationResultsResponse']
export type JobBatchOpView = 'active' | 'ended' | 'archived'

// 列表/归档（M61）姊妹分列在 jobBatchOperationsListApi.ts（体积预算），
// re-export 保持单入口 import 便利。
export type {
  JobBatchOperationListItem,
  JobBatchOperationListResponse,
  JobBatchOperationArchiveResponse,
} from './jobBatchOperationsListApi'
export {
  listJobBatchOperations,
  archiveJobBatchOperation,
} from './jobBatchOperationsListApi'

/** 批量端点的返回：≤ 阈值走同步结果，超阈值返回 202 受理句柄。 */
export type BatchOpMaybeAccepted<T> = T | JobBatchOperationAccepted

/** 判别 202 受理体。同步结果顶层没有 operation 键（operation 只出现在逐
 * job 结果项内），这里再双键校验 id/kind 防误命中。 */
export function isJobBatchAccepted(
  data: unknown
): data is JobBatchOperationAccepted {
  if (typeof data !== 'object' || data === null || !('operation' in data)) {
    return false
  }
  const operation = (data as { operation?: unknown }).operation
  return (
    typeof operation === 'object' &&
    operation !== null &&
    typeof (operation as { id?: unknown }).id === 'string' &&
    typeof (operation as { kind?: unknown }).kind === 'string'
  )
}

/** 单 op 轮询：状态 + 计数 + reason_counts + 终态 output（package 下载）。 */
export async function getJobBatchOperation(
  workspaceId: string,
  opId: string
): Promise<JobBatchOperation> {
  return api<JobBatchOperation>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/job-batch-operations/${encodeURIComponent(opId)}`
  )
}

/** 逐 job 结果（keyset 分页；cursor 取上一页的 next_cursor）。 */
export async function listJobBatchOperationResults(
  workspaceId: string,
  opId: string,
  options: { limit?: number; cursor?: string | null } = {}
): Promise<JobBatchOperationResultsPage> {
  const params = new URLSearchParams()
  if (options.limit != null) params.set('limit', String(options.limit))
  if (options.cursor) params.set('cursor', options.cursor)
  const query = params.size > 0 ? `?${params}` : ''
  return api<JobBatchOperationResultsPage>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/job-batch-operations/${encodeURIComponent(opId)}/results${query}`
  )
}
