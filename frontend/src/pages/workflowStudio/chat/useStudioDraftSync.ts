import { useEffect, useRef } from 'react'
import { updateStudioChatContext } from './studioChatApi'

/** 画布 draft 同步：draftYaml 变化后 debounce 约 400ms 把最新草稿 PUT 到会话
 * 上下文（draft_yaml 字段），agent 调 get_studio_context 时读到的是编辑中的
 * 实时草稿而不是建会话时的快照。debounce 避免打字期间每个按键都推送。
 * 去重（lastSent ref）避免无关重渲染/会话切换回声触发重复推送；推送失败清除
 * 去重标记——下次 draft 变化或会话切换会重试，上下文只是提示性信息，不阻断编辑。 */
export function useStudioDraftSync(
  workspaceId: string | undefined,
  sessionId: string | null,
  draftYaml: string | null
) {
  const lastSentRef = useRef<{ sessionId: string; yaml: string } | null>(null)
  useEffect(() => {
    if (!workspaceId || !sessionId || draftYaml === null) return
    const last = lastSentRef.current
    if (last && last.sessionId === sessionId && last.yaml === draftYaml) return
    lastSentRef.current = { sessionId, yaml: draftYaml }
    // debounce：变化持续发生时反复重置计时器，停下约 400ms 才真正推送；
    // cleanup 在依赖变化/卸载时清掉未触发的计时器。
    const timer = setTimeout(() => {
      void updateStudioChatContext(workspaceId, sessionId, { draftYaml }).catch(
        // 推送失败清除去重标记，下次 draft 变化/会话切换时重试。
        () => {
          if (lastSentRef.current?.sessionId === sessionId) {
            lastSentRef.current = null
          }
        }
      )
    }, 400)
    return () => clearTimeout(timer)
  }, [workspaceId, sessionId, draftYaml])
}
