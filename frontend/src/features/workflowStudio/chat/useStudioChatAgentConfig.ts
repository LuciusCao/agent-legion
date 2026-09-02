import { useCallback, useEffect, useRef, useState } from 'react'
import type { StudioChatSessionRecord } from './studioChatApi'
import {
  setStudioChatConfigOption,
  setStudioChatMode,
} from './studioChatConfigApi'

/** agent 配置切换（#368）。切换成功的响应作为「本地覆盖」立即生效——只在它
 * 所基于的会话快照仍是当前快照时（SSE 推来的更新快照永远更新：并发通知/
 * 他人切换以 agent 真值为准，在途请求的旧响应不得覆盖）。失败信息本地展示，
 * 不进 useStudioChat 的 actionError（预算与关注点分离）。 */
export function useStudioChatAgentConfig(
  workspaceId: string | undefined,
  session: StudioChatSessionRecord | null
) {
  const [local, setLocal] = useState<{
    base: StudioChatSessionRecord | null
    record: StudioChatSessionRecord
  } | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const [lastAction, setLastAction] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const effective = local && local.base === session ? local.record : session

  const run = useCallback(
    async (
      key: string,
      call: (ws: string, id: string) => Promise<StudioChatSessionRecord>
    ) => {
      if (!workspaceId || !session) return
      setPending(key)
      setLastAction(key)
      setError(null)
      try {
        setLocal({ base: session, record: await call(workspaceId, session.id) })
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : '切换失败')
      } finally {
        setPending(null)
      }
    },
    [workspaceId, session]
  )
  const setMode = useCallback(
    (modeId: string) =>
      run('mode', (ws, id) => setStudioChatMode(ws, id, modeId)),
    [run]
  )
  const setOption = useCallback(
    (configId: string, value: string) =>
      run(configId, (ws, id) =>
        setStudioChatConfigOption(ws, id, configId, value)
      ),
    [run]
  )
  return { session: effective, pending, lastAction, error, setMode, setOption }
}

/** 模型切换后档位联动的漂移提示：思考档位的当前值变了、而最近一次动作不是
 * 用户自己切档 → 提示；用户切档引起的变化不提示。 */
export function useThoughtDrift(
  current: string | null,
  ownChange: boolean
): string | null {
  const previous = useRef(current)
  const [drift, setDrift] = useState<string | null>(null)
  useEffect(() => {
    if (previous.current === current) return
    setDrift(
      ownChange || current === null
        ? null
        : `思考档位已随模型切换变为 ${current}`
    )
    previous.current = current
  }, [current, ownChange])
  return drift
}
