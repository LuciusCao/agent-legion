import { useCallback, useState } from 'react'
import { resumeStudioChatSession } from './studioChatResumeApi'
import type { StudioChatSessionRecord } from './studioChatApi'

type RunAction = (action: () => Promise<void>) => Promise<boolean>

/** 「继续对话」动作：resume 成功后把 idle 快照刷回会话状态，输入随之解禁；
 * 失败经 runAction 落 actionError 由面板展示。applySession 由调用方包装：
 * 在途切换会话的归属守卫与 sessions 列表缓存失效都在那层（useStudioChat）。 */
export function useStudioChatResume(
  workspaceId: string | undefined,
  activeSessionId: string | null,
  runAction: RunAction,
  applySession: (session: StudioChatSessionRecord) => void
) {
  const [resuming, setResuming] = useState(false)
  const resume = useCallback(async () => {
    if (!workspaceId || !activeSessionId) return
    setResuming(true)
    await runAction(async () => {
      applySession(await resumeStudioChatSession(workspaceId, activeSessionId))
    })
    setResuming(false)
  }, [workspaceId, activeSessionId, runAction, applySession])
  return { resume, resuming }
}
