import { useCallback, useState } from 'react'
import { resumeStudioChatSession } from './studioChatResumeApi'
import type { StudioChatSessionRecord } from './studioChatApi'

type RunAction = (action: () => Promise<void>) => Promise<boolean>

/** 「继续对话」动作：resume 成功后把 idle 快照刷回会话状态，输入随之解禁；
 * 失败经 runAction 落 actionError 由面板展示。 */
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
