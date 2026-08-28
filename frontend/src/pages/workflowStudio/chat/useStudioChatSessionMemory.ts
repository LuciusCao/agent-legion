import { useEffect } from 'react'
import type { StudioChatSessionRecord } from './studioChatApi'

const KEY_PREFIX = 'studio-chat.active-session.'

export function readRememberedSessionId(workspaceId: string): string | null {
  try {
    return window.localStorage.getItem(KEY_PREFIX + workspaceId)
  } catch {
    return null
  }
}

/** 按 workspace 记忆选中会话：重进 Studio 优先恢复上次选择，已不存在
 * （被删/跨实例）则回落最近会话（原面板自动选择逻辑，随本 hook 迁入）。 */
export function useStudioChatSessionMemory(
  workspaceId: string | undefined,
  sessions: StudioChatSessionRecord[],
  activeSessionId: string | null,
  selectSession: (sessionId: string) => void
) {
  // 只在有选中时写入：初始 null（尚未恢复）不得清掉记忆值，否则同 commit
  // 的恢复效应读到的就是空。
  useEffect(() => {
    if (!workspaceId || !activeSessionId) return
    try {
      window.localStorage.setItem(KEY_PREFIX + workspaceId, activeSessionId)
    } catch {
      // localStorage 不可用（隐私模式等）时静默降级为不记忆。
    }
  }, [workspaceId, activeSessionId])

  useEffect(() => {
    if (!workspaceId || activeSessionId !== null || sessions.length === 0)
      return
    const remembered = readRememberedSessionId(workspaceId)
    const target = sessions.find((row) => row.id === remembered) ?? sessions[0]
    selectSession(target.id)
  }, [workspaceId, activeSessionId, sessions, selectSession])
}
