import { useEffect, useRef } from 'react'
import {
  useStudioChat,
  type StudioChat,
} from '../workflowStudio/chat/useStudioChat'
import {
  buildDiagnosisPrimer,
  type JobDiagnosisTarget,
} from './jobDiagnosisContext'

export type JobDiagnosisChat = {
  chat: StudioChat
  /** 会话创建失败的错误（来自 chat.actionError，仅当还没有会话时）。 */
  bootstrapError: string | null
  retryBootstrap: () => void
}

/** 排查对话的会话引导（#329）：面板挂载即为激活——按 workspace 建会话
 * （跟随 agent 列表第一项，与 Studio 面板同一默认），会话进入 idle 后自动
 * 发送携带 workspace+job+node 的 primer 消息，agent 无需用户手工复制任何
 * 信息即可开工。复用 useStudioChat 的全部传输/状态机，不加自有协议。
 *
 * 全程用 ref 做一次性标记（无 effect 内 setState）。「哪个会话收 primer」
 * 的捕获规则：boot 后 starting 回落、无 actionError 且 session 非空——即
 * startSession 成功落地的那一刻；会话记忆选中的旧会话（starting 期间）与
 * 建会话失败（actionError 置位）都不满足，多面板并存也各自捕获各自的。 */
export function useJobDiagnosis(
  workspaceId: string,
  target: JobDiagnosisTarget
): JobDiagnosisChat {
  const chat = useStudioChat(workspaceId)
  const bootedRef = useRef(false)
  const createdSessionRef = useRef<string | null>(null)
  const primedSessionRef = useRef<string | null>(null)

  // 引导：agent 列表就绪后建一次会话（picker 第一项 = 本机可用 agent）。
  const agentsReady = !chat.agentsLoading && !chat.agentsError
  useEffect(() => {
    if (!agentsReady || chat.agents.length === 0 || bootedRef.current) return
    bootedRef.current = true
    void chat.startSession(chat.agents[0].id)
  }, [agentsReady, chat.agents, chat])

  // 捕获 + primer 同 effect（ref 只在 effect 内读写）：boot 后 starting 回落、
  // 无 actionError 且 session 非空 = startSession 成功落地；再等会话进入 idle
  // （ACP 握手完成）发 primer——starting 状态的会话会被后端判 busy
  // （claim idle->running）。
  const sessionStatus = chat.session?.status ?? null
  useEffect(() => {
    if (!bootedRef.current || primedSessionRef.current) return
    if (chat.starting || chat.actionError || !chat.session) return
    const sessionId = chat.session.id
    createdSessionRef.current = sessionId
    if (chat.activeSessionId !== sessionId) return
    if (sessionStatus !== 'idle') return
    primedSessionRef.current = sessionId
    void chat.send(buildDiagnosisPrimer(target))
    // target 在面板生命周期内固定（一次打开对应一个 job/node）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    chat.starting,
    chat.actionError,
    chat.session,
    chat.activeSessionId,
    sessionStatus,
    chat,
  ])

  return {
    chat,
    bootstrapError: chat.session ? null : chat.actionError,
    retryBootstrap: () => {
      bootedRef.current = false
      createdSessionRef.current = null
      primedSessionRef.current = null
    },
  }
}
