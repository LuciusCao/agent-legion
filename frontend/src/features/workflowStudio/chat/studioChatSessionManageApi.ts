import { api } from '../../../api/core'
import type { components } from '../../../generated/api'
import type { StudioChatSessionRecord } from './studioChatApi'

type S = components['schemas']
type SessionResponse = S['StudioChatSessionResponse']
type UpdateRequest = S['StudioChatSessionUpdateRequest']
type DeleteResponse = S['StudioChatSessionDeleteResponse']

function sessionUrl(workspaceId: string, sessionId: string): string {
  return `/api/workspaces/${encodeURIComponent(workspaceId)}/studio-chat/sessions/${encodeURIComponent(sessionId)}`
}

/** 会话改名（#872）：后端去首尾空白；空串回落默认标签「对话 <时间>」。
 * 独立成模块（studioChatApi.ts 文件预算），测试按模块 mock。 */
export function renameStudioChatSession(
  workspaceId: string,
  sessionId: string,
  title: string
): Promise<StudioChatSessionRecord> {
  const body: UpdateRequest = { title }
  return api<SessionResponse>(sessionUrl(workspaceId, sessionId), {
    method: 'PATCH',
    body: JSON.stringify(body),
  }).then((response) => response.session)
}

/** 会话删除（#872，软删）：运行中的会话由后端先关闭 runtime；删除后该会话
 * 从列表消失，任何会话级接口都按不存在返回 404。DELETE 动词保留给既有的
 * 「关闭会话」语义。 */
export function deleteStudioChatSession(
  workspaceId: string,
  sessionId: string
): Promise<void> {
  return api<DeleteResponse>(`${sessionUrl(workspaceId, sessionId)}/delete`, {
    method: 'POST',
  }).then(() => undefined)
}
