import type { Dispatch, MutableRefObject, SetStateAction } from 'react'
import {
  cancelStudioChatTurn,
  sendStudioChatMessage,
  setStudioChatAllowAll,
  type StudioChatSessionRecord,
} from './studioChatApi'
import { upsertMessage, type ChatMessage } from './studioChatMessages'

/** 会话动作的归属上下文：发起时的 workspace / 选中会话快照、当前选中会话
 * ref（落地时复核基准）与可选的调用方断言 expected。 */
type StudioChatSessionActionContext = {
  workspaceId: string | undefined
  activeSessionId: string | null
  activeSessionIdRef: MutableRefObject<string | null>
  setActionError: (message: string | null) => void
  expected?: string
}

function ownsSession(
  ctx: StudioChatSessionActionContext,
  sessionId: string,
  op: string
) {
  if (ctx.activeSessionIdRef.current === sessionId) return true
  console.warn(
    `[studio-chat] ${op} 会话归属失配，已丢弃（发起 ${sessionId}，当前 ${ctx.activeSessionIdRef.current ?? '无'}）`
  )
  return false
}

/** #962：send / cancel / setAllowAll 的会话归属守卫（runAction 的会话版）。
 * 发起前只断言调用方显式传入的 expected 与发起时的选中会话一致；真正的
 * 防护在 await 之后：与 activeSessionIdRef 复核——
 * 切换会话后旧会话的迟到结果（消息、快照、错误）一律丢弃并留 warn 日志。
 * 返回 null 表示未发起、已丢弃或失败（失败原因已置 actionError）。 */
async function runStudioChatSessionAction<T>(
  ctx: StudioChatSessionActionContext,
  op: string,
  action: (workspaceId: string, sessionId: string) => Promise<T>
): Promise<{ value: T } | null> {
  const { workspaceId, activeSessionId: sessionId } = ctx
  if (!workspaceId || !sessionId) return null
  if (ctx.expected !== undefined && ctx.expected !== sessionId) {
    console.warn(
      `[studio-chat] ${op} 会话归属失配，已丢弃（期望 ${ctx.expected}，当前 ${sessionId}）`
    )
    return null
  }
  // 发请求前不比对 activeSessionIdRef：它由 passive effect 同步，会话切换
  // commit 与 effect flush 之间有毫秒级窗口，此时预检会静默丢弃合法点击。
  // 归属复核只放在 await 之后。
  ctx.setActionError(null)
  try {
    const value = await action(workspaceId, sessionId)
    return ownsSession(ctx, sessionId, op) ? { value } : null
  } catch (error) {
    if (ownsSession(ctx, sessionId, op))
      ctx.setActionError(error instanceof Error ? error.message : '操作失败')
    return null
  }
}

/** useStudioChat 的会话级动作（#962 拆出，保体积预算）：send / cancel /
 * setAllowAll 均经 runStudioChatSessionAction 做归属守卫。可选的
 * expectedSessionId 由调用方传入渲染时绑定的会话 id，失配即丢弃。 */
export function useStudioChatSessionActions(deps: {
  workspaceId: string | undefined
  activeSessionId: string | null
  activeSessionIdRef: MutableRefObject<string | null>
  setActionError: (message: string | null) => void
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>
  setSession: Dispatch<SetStateAction<StudioChatSessionRecord | null>>
}) {
  const { setMessages, setSession } = deps
  const ctx = (expected?: string): StudioChatSessionActionContext => ({
    workspaceId: deps.workspaceId,
    activeSessionId: deps.activeSessionId,
    activeSessionIdRef: deps.activeSessionIdRef,
    setActionError: deps.setActionError,
    expected,
  })

  // 返回是否发送成功：busy 排队（useStudioChatQueue）flush 失败时要保留
  // 队首，失败原因已置 actionError。
  async function send(text: string, expectedSessionId?: string) {
    if (!text.trim()) return false
    const result = await runStudioChatSessionAction(
      ctx(expectedSessionId),
      'send',
      (wsId, sessionId) => sendStudioChatMessage(wsId, sessionId, text.trim())
    )
    if (!result) return false
    const message = result.value
    setMessages((current) => upsertMessage(current, message) ?? current)
    return true
  }

  async function cancel(expectedSessionId?: string) {
    const result = await runStudioChatSessionAction(
      ctx(expectedSessionId),
      'cancel',
      cancelStudioChatTurn
    )
    if (result) setSession(result.value)
  }

  async function setAllowAll(enabled: boolean, expectedSessionId?: string) {
    const result = await runStudioChatSessionAction(
      ctx(expectedSessionId),
      'setAllowAll',
      (wsId, sessionId) => setStudioChatAllowAll(wsId, sessionId, enabled)
    )
    if (result) setSession(result.value)
  }

  return { send, cancel, setAllowAll }
}
