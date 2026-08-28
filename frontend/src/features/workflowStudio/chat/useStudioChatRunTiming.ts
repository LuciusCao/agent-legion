import { useEffect, useState } from 'react'

const BUSY_STATUSES = new Set(['starting', 'running', 'awaiting_permission'])

export function isStudioChatBusy(status: string): boolean {
  return BUSY_STATUSES.has(status)
}

/** run 计时（从 useStudioChat.ts 拆出，文件预算）：会话状态快照进出 busy
 * 状态时记开始/用时；切换会话即重置。 */
export function useStudioChatRunTiming(
  sessionStatus: string | null,
  sessionId: string | null
) {
  const [runTiming, setRunTiming] = useState<{
    startedAt: number | null
    lastMs: number | null
  }>({ startedAt: null, lastMs: null })
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 会话切换时重置计时（与原 useStudioChat 内联逻辑一致）
    setRunTiming({ startedAt: null, lastMs: null })
  }, [sessionId])
  useEffect(() => {
    if (sessionStatus === null) return
    const busy = BUSY_STATUSES.has(sessionStatus)
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 计时器跟随 SSE 会话状态快照翻转
    setRunTiming((previous) => {
      if (busy) {
        return { startedAt: previous.startedAt ?? Date.now(), lastMs: null }
      }
      if (previous.startedAt === null) return previous
      return { startedAt: null, lastMs: Date.now() - previous.startedAt }
    })
  }, [sessionStatus])
  return runTiming
}
