import { api } from '../../../api/core'
import type { components } from '../../../generated/api'
import type { StudioChatSessionRecord } from './studioChatApi'

type SessionResponse = components['schemas']['StudioChatSessionResponse']

/** closed/error 会话的「继续对话」：后端重建 agent runtime 后返回 idle 快照。
 * 独立成模块（studioChatApi.ts 文件预算），测试按模块 mock。 */
export function resumeStudioChatSession(
  workspaceId: string,
  sessionId: string
): Promise<StudioChatSessionRecord> {
  return api<SessionResponse>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/studio-chat/sessions/${encodeURIComponent(sessionId)}/resume`,
    { method: 'POST' }
  ).then((response) => response.session)
}
