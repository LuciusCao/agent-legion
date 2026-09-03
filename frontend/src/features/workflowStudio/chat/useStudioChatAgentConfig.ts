import { useCallback, useState } from 'react'
import type { StudioChatSessionRecord } from './studioChatApi'
import {
  setStudioChatConfigOption,
  setStudioChatMode,
} from './studioChatConfigApi'

type Scoped<T> = { sessionId: string; value: T }

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
  // pending / lastAction / error 都带着所属会话 id：切到别的会话后，旧会话
  // 在途请求的失败与禁用状态不得串到新会话（Codex P2 on PR #398）。
  const [pending, setPending] = useState<Scoped<string> | null>(null)
  const [lastAction, setLastAction] = useState<Scoped<string> | null>(null)
  const [error, setError] = useState<Scoped<string> | null>(null)
  const effective = local && local.base === session ? local.record : session
  const sessionId = session?.id ?? null
  const scoped = <T>(value: Scoped<T> | null): T | null =>
    value !== null && value.sessionId === sessionId ? value.value : null

  const run = useCallback(
    async (
      key: string,
      call: (ws: string, id: string) => Promise<StudioChatSessionRecord>
    ) => {
      if (!workspaceId || !session) return
      const owner = session.id
      setPending({ sessionId: owner, value: key })
      setLastAction({ sessionId: owner, value: key })
      setError(null)
      try {
        setLocal({ base: session, record: await call(workspaceId, owner) })
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : '切换失败'
        setError({ sessionId: owner, value: message })
      } finally {
        // 只清自己那次的 pending：更晚发起的（同会话或别的会话）不受影响。
        setPending((current) =>
          current?.sessionId === owner && current.value === key ? null : current
        )
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
  return {
    session: effective,
    pending: scoped(pending),
    lastAction: scoped(lastAction),
    error: scoped(error),
    setMode,
    setOption,
  }
}
