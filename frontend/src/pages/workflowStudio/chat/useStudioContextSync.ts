import { useEffect, useRef } from 'react'
import { updateStudioChatContext } from './studioChatApi'

/** 选中节点同步：选中变化时把最新值 PUT 到会话上下文，agent 调
 * get_studio_context 时读到的是实时值而不是建会话时的快照。
 * 去重（lastSent ref）避免会话快照 SSE 回声触发重复推送；失败静默并清除
 * 去重标记——下次选中变化或会话切换会重试，上下文只是提示性信息，不阻断对话。 */
export function useStudioContextSync(
  workspaceId: string | undefined,
  sessionId: string | null,
  selectedNodeKey: string | null
) {
  const lastSentRef = useRef<{ sessionId: string; key: string | null } | null>(
    null
  )
  useEffect(() => {
    if (!workspaceId || !sessionId) return
    const last = lastSentRef.current
    if (last && last.sessionId === sessionId && last.key === selectedNodeKey) {
      return
    }
    lastSentRef.current = { sessionId, key: selectedNodeKey }
    void updateStudioChatContext(workspaceId, sessionId, selectedNodeKey).catch(
      // 推送失败清除去重标记，下次选中变化/会话切换时重试。
      () => {
        if (lastSentRef.current?.sessionId === sessionId) {
          lastSentRef.current = null
        }
      }
    )
  }, [workspaceId, sessionId, selectedNodeKey])
}
