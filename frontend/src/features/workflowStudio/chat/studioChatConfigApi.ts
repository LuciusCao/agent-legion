import { api } from '../../../api/core'
import type { components } from '../../../generated/api'
import type { StudioChatSessionRecord } from './studioChatApi'

type SessionResponse = components['schemas']['StudioChatSessionResponse']

function base(workspaceId: string, sessionId: string): string {
  return `/api/workspaces/${encodeURIComponent(workspaceId)}/studio-chat/sessions/${encodeURIComponent(sessionId)}`
}

/** agent 侧会话配置切换（#368）：服务端按会话当前广告列表白名单校验，越界
 * 400、agent 拒绝/超时 409。独立成模块（studioChatApi.ts 文件预算），测试按模块 mock。 */
export function setStudioChatMode(
  workspaceId: string,
  sessionId: string,
  modeId: string
): Promise<StudioChatSessionRecord> {
  return api<SessionResponse>(`${base(workspaceId, sessionId)}/mode`, {
    method: 'POST',
    body: JSON.stringify({ mode_id: modeId }),
  }).then((response) => response.session)
}

export function setStudioChatConfigOption(
  workspaceId: string,
  sessionId: string,
  configId: string,
  value: string
): Promise<StudioChatSessionRecord> {
  return api<SessionResponse>(
    `${base(workspaceId, sessionId)}/config-options`,
    {
      method: 'POST',
      body: JSON.stringify({ config_id: configId, value }),
    }
  ).then((response) => response.session)
}
