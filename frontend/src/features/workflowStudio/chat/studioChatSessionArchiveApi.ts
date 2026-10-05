import { api } from '../../../api/core'
import type { components } from '../../../generated/api'
import type { StudioChatSessionRecord } from './studioChatApi'

type S = components['schemas']
type SessionResponse = S['StudioChatSessionResponse']
type SessionsResponse = S['StudioChatSessionsResponse']

function sessionsUrl(workspaceId: string): string {
  return `/api/workspaces/${encodeURIComponent(workspaceId)}/studio-chat/sessions`
}

function sessionUrl(workspaceId: string, sessionId: string): string {
  return `${sessionsUrl(workspaceId)}/${encodeURIComponent(sessionId)}`
}

/** 归档视图（#924）：默认会话列表不含已归档会话，这里只取已归档的。
 * 独立成模块（studioChatApi.ts / studioChatSessionManageApi.ts 文件预算）。 */
export function fetchArchivedStudioChatSessions(
  workspaceId: string
): Promise<StudioChatSessionRecord[]> {
  return api<SessionsResponse>(
    `${sessionsUrl(workspaceId)}?archived=true`
  ).then((response) => response.sessions)
}

/** 归档（#924，可恢复）：运行中的会话由后端先按「关闭」路径收尾（撤销
 * run token）；归档后从默认列表隐藏，「继续对话」需先取消归档。 */
export function archiveStudioChatSession(
  workspaceId: string,
  sessionId: string
): Promise<StudioChatSessionRecord> {
  return api<SessionResponse>(`${sessionUrl(workspaceId, sessionId)}/archive`, {
    method: 'POST',
  }).then((response) => response.session)
}

/** 取消归档（#924）：只清归档标记、不拉起 runtime——会话回到列表仍是
 * 已关闭，按既有「继续对话」路径恢复。 */
export function unarchiveStudioChatSession(
  workspaceId: string,
  sessionId: string
): Promise<StudioChatSessionRecord> {
  return api<SessionResponse>(
    `${sessionUrl(workspaceId, sessionId)}/unarchive`,
    { method: 'POST' }
  ).then((response) => response.session)
}
