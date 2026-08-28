import { useEffect, useRef } from 'react'
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
  // 的恢复效应读到的就是空。workspace 刚切换（React Router 复用组件实例）
  // 时 activeSessionId 还是旧 workspace 的残留选中，同样不得写入——
  // useStudioChat 的重置效应会在下一 render 把它归零，再由恢复效应按新
  // workspace 的记忆重新选择。
  const previousWorkspaceRef = useRef(workspaceId)
  useEffect(() => {
    const staleWorkspace = previousWorkspaceRef.current !== workspaceId
    previousWorkspaceRef.current = workspaceId
    if (!workspaceId || !activeSessionId || staleWorkspace) return
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
