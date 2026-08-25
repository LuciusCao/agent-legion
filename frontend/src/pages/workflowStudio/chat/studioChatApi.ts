import { api } from '../../../api/core'
import type { components } from '../../../generated/api'

type S = components['schemas']

export type StudioChatAgentOption = S['StudioChatAgentOption']
export type StudioChatSessionRecord = S['StudioChatSessionRecord']
export type StudioChatMessageRecord = S['StudioChatMessageRecord']
export type StudioChatPermissionAnswerRequest =
  S['StudioChatPermissionAnswerRequest']

type AgentsResponse = S['StudioChatAgentsResponse']
type SessionsResponse = S['StudioChatSessionsResponse']
type SessionResponse = S['StudioChatSessionResponse']
type MessagesResponse = S['StudioChatMessagesResponse']
type MessageResponse = S['StudioChatMessageResponse']

function base(workspaceId: string, sessionId?: string): string {
  const root = `/api/workspaces/${encodeURIComponent(workspaceId)}/studio-chat`
  return sessionId
    ? `${root}/sessions/${encodeURIComponent(sessionId)}`
    : `${root}/sessions`
}

export function fetchStudioChatAgents(
  workspaceId: string
): Promise<StudioChatAgentOption[]> {
  return api<AgentsResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/studio-chat/agents`
  ).then((response) => response.agents)
}

export function fetchStudioChatSessions(
  workspaceId: string
): Promise<StudioChatSessionRecord[]> {
  return api<SessionsResponse>(base(workspaceId)).then(
    (response) => response.sessions
  )
}

export function fetchStudioChatSession(
  workspaceId: string,
  sessionId: string
): Promise<StudioChatSessionRecord> {
  return api<SessionResponse>(base(workspaceId, sessionId)).then(
    (response) => response.session
  )
}

export function createStudioChatSession(
  workspaceId: string,
  agentId: string
): Promise<StudioChatSessionRecord> {
  return api<SessionResponse>(base(workspaceId), {
    method: 'POST',
    body: JSON.stringify({ agent_id: agentId, title: '' }),
  }).then((response) => response.session)
}

export function fetchStudioChatMessages(
  workspaceId: string,
  sessionId: string,
  afterSeq = 0
): Promise<StudioChatMessageRecord[]> {
  return api<MessagesResponse>(
    `${base(workspaceId, sessionId)}/messages?after_seq=${afterSeq}`
  ).then((response) => response.messages)
}

export function sendStudioChatMessage(
  workspaceId: string,
  sessionId: string,
  text: string
): Promise<StudioChatMessageRecord> {
  return api<MessageResponse>(`${base(workspaceId, sessionId)}/messages`, {
    method: 'POST',
    body: JSON.stringify({ text }),
  }).then((response) => response.message)
}

export function cancelStudioChatTurn(
  workspaceId: string,
  sessionId: string
): Promise<StudioChatSessionRecord> {
  return api<SessionResponse>(`${base(workspaceId, sessionId)}/cancel`, {
    method: 'POST',
  }).then((response) => response.session)
}

export function setStudioChatAllowAll(
  workspaceId: string,
  sessionId: string,
  enabled: boolean
): Promise<StudioChatSessionRecord> {
  return api<SessionResponse>(
    `${base(workspaceId, sessionId)}/permissions/allow-all`,
    { method: 'POST', body: JSON.stringify({ enabled }) }
  ).then((response) => response.session)
}

export function answerStudioChatPermission(
  workspaceId: string,
  sessionId: string,
  requestId: string,
  answer: StudioChatPermissionAnswerRequest
): Promise<void> {
  return api(
    `${base(workspaceId, sessionId)}/permissions/${encodeURIComponent(requestId)}`,
    { method: 'POST', body: JSON.stringify(answer) }
  ).then(() => undefined)
}

/** 把 Studio 侧上下文推到会话（agent 经 get_studio_context 工具读取实时值）。
 * 部分更新：body 只带出现的字段——selectedNodeKey 出现即使为 null 也发送（清除选中），draftYaml 映射为 draft_yaml。 */
export function updateStudioChatContext(
  workspaceId: string,
  sessionId: string,
  updates: { selectedNodeKey?: string | null; draftYaml?: string }
): Promise<StudioChatSessionRecord> {
  const body: Record<string, unknown> = {}
  if ('selectedNodeKey' in updates)
    body.selected_node_key = updates.selectedNodeKey
  if (updates.draftYaml !== undefined) body.draft_yaml = updates.draftYaml
  return api<SessionResponse>(`${base(workspaceId, sessionId)}/context`, {
    method: 'PUT',
    body: JSON.stringify(body),
  }).then((response) => response.session)
}
