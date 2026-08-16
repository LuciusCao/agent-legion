import { api } from '../../../api/core'
import type { components } from '../../../generated/api'

export type StudioChatAgentOption =
  components['schemas']['StudioChatAgentOption']
export type StudioChatSessionRecord =
  components['schemas']['StudioChatSessionRecord']
export type StudioChatMessageRecord =
  components['schemas']['StudioChatMessageRecord']
export type StudioChatPermissionAnswerRequest =
  components['schemas']['StudioChatPermissionAnswerRequest']

type AgentsResponse = components['schemas']['StudioChatAgentsResponse']
type SessionsResponse = components['schemas']['StudioChatSessionsResponse']
type SessionResponse = components['schemas']['StudioChatSessionResponse']
type MessagesResponse = components['schemas']['StudioChatMessagesResponse']
type MessageResponse = components['schemas']['StudioChatMessageResponse']

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
    {
      method: 'POST',
      body: JSON.stringify(answer),
    }
  ).then(() => undefined)
}

/** 把用户在 Studio 当前选中的节点推到会话上下文（agent 经
 * get_studio_context 工具读取实时值）。 */
export function updateStudioChatContext(
  workspaceId: string,
  sessionId: string,
  selectedNodeKey: string | null
): Promise<StudioChatSessionRecord> {
  return api<SessionResponse>(`${base(workspaceId, sessionId)}/context`, {
    method: 'PUT',
    body: JSON.stringify({ selected_node_key: selectedNodeKey }),
  }).then((response) => response.session)
}
