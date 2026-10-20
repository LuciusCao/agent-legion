import { api } from './core'
import { BULK_REQUEST_TIMEOUT } from './requestTimeout'
import type {
  FailedNodeRunsResponse,
  JobRerunByFailureRequest,
  JobRerunByFailureResponse,
} from '../types/failureTypes'

/** 单页 failed-node-runs（#713 keyset 分页）；cursor 取上一页的 next_cursor。 */
export async function fetchFailedNodeRuns(
  workspaceId: string,
  cursor?: string
): Promise<FailedNodeRunsResponse> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
  return api<FailedNodeRunsResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/failed-node-runs${query}`
  )
}

export async function rerunJobsByFailure(
  workspaceId: string,
  body: JobRerunByFailureRequest
): Promise<JobRerunByFailureResponse> {
  return api<JobRerunByFailureResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs/rerun-by-failure`,
    // 与其他批量端点一致豁免超时：服务端同步跑完整批，客户端 2 分钟超时
    // 并不撤销服务端动作，只会误报失败（#719 / #1348）。
    {
      method: 'POST',
      body: JSON.stringify(body),
      timeoutMs: BULK_REQUEST_TIMEOUT,
    }
  )
}
