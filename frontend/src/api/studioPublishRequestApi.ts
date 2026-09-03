import { api } from './core'
import type { components } from '../generated/api'

export type StudioPublishRequestRecord =
  components['schemas']['StudioPublishRequestRecord']
type PendingResponse =
  components['schemas']['StudioPublishRequestPendingResponse']
type ResolveResponse =
  components['schemas']['StudioPublishRequestResolveResponse']

/** Agent 发起的发布请求（#416）：workspace 当前 pending 的请求，null 表示
 * 没有（Studio 前端轮询此端点弹发布确认对话框）。独立成模块：
 * workflows.ts 预算顶格（同 studioChatConfigApi 的先例）。 */
export async function fetchPendingPublishRequest(
  workspaceId: string
): Promise<StudioPublishRequestRecord | null> {
  const response = await api<PendingResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-drafts/publish-request`
  )
  return response.request
}

/** 用户确认 agent 的发布请求：后端走与手动发布一致的门禁（同一
 * publish_workflow_draft 路径），成功返回落定后的请求行。 */
export async function confirmPublishRequest(
  workspaceId: string,
  requestId: string
): Promise<StudioPublishRequestRecord> {
  const response = await api<ResolveResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-drafts/publish-request/${encodeURIComponent(requestId)}/confirm`,
    { method: 'POST' }
  )
  return response.request
}

/** 用户取消 agent 的发布请求（rejected）：agent 可继续修改草稿再发起。 */
export async function cancelPublishRequest(
  workspaceId: string,
  requestId: string
): Promise<StudioPublishRequestRecord> {
  const response = await api<ResolveResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/workflow-drafts/publish-request/${encodeURIComponent(requestId)}/cancel`,
    { method: 'POST' }
  )
  return response.request
}
