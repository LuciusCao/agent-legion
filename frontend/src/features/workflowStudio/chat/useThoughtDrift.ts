import { useEffect, useRef, useState } from 'react'

/** 模型切换后档位联动的漂移判定（PR #398 review）：切会话不是漂移（换 sessionId
 * 时静默重置基线）；用户自己切档引起的那一次变化不算（ownToken 是发起切换时
 * 生成的对象，吞掉一次即视为已消费，之后 agent 自主变档照常提示）。 */
export function useThoughtDrift(
  sessionId: string | null,
  current: string | null,
  ownToken: object | null
): boolean {
  const seen = useRef({ sessionId, current, consumed: null as object | null })
  const [drifted, setDrifted] = useState(false)
  useEffect(() => {
    const prev = seen.current
    const own = ownToken !== null && ownToken !== prev.consumed
    const changed = prev.sessionId === sessionId && prev.current !== current
    seen.current = {
      sessionId,
      current,
      consumed: changed && own ? ownToken : prev.consumed,
    }
    if (prev.sessionId !== sessionId) setDrifted(false)
    else if (changed) setDrifted(!own && current !== null)
  }, [sessionId, current, ownToken])
  return drifted
}
