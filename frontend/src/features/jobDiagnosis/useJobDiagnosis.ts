import { useEffect, useRef, useState } from 'react'
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
  /** 配置芯片未绑定本次唤起会话前锁定（#801 codex 轮 5 P2）：bootstrap 在途
   * 或失败时，chips 展示的是会话记忆恢复的历史会话——不变更一律只读，
   * 否则变更会提交到历史会话 ID。 */
  configLocked: boolean
  /** 会话创建失败的错误（来自 chat.actionError；本次 bootstrap 失败时如实
   * 呈现，不被恢复的历史会话掩盖；尚无会话时同原语义）。 */
  bootstrapError: string | null
  retryBootstrap: () => void
}

/** 排查对话的会话引导（#329）：面板挂载即为激活——按 workspace 建会话
 * （跟随 agent 列表第一项，与 Studio 面板同一默认），会话进入 idle 后自动
 * 发送携带 workspace+job+node 的 primer 消息，agent 无需用户手工复制任何
 * 信息即可开工。复用 useStudioChat 的全部传输/状态机，不加自有协议。
 *
 * 全程用 ref 做一次性标记（primer 落点不走 render），绑定/失败态走 state
 * （lint 禁渲染期读 ref）。「哪个会话收 primer」的捕获规则：boot 后
 * starting 回落、无 actionError 且 session 非空——即 startSession 成功落地
 * 的那一刻。绑定态（#801 codex 轮 5 P2）：解锁条件不是 starting 回落，而是
 * 「本次唤起创建的会话已激活」——attemptSettled 闸住「starting 被批处理
 * 跳过渲染」的时序，失败闩锁闸住「恢复回填把 actionError 抹掉」的时序；
 * create 失败时 chips 保持锁定、创建错误如实呈现，不被恢复的历史会话
 * 掩盖。 */
export function useJobDiagnosis(
  workspaceId: string,
  target: JobDiagnosisTarget
): JobDiagnosisChat {
  const chat = useStudioChat(workspaceId)
  const bootedRef = useRef(false)
  const createdSessionRef = useRef<string | null>(null)
  const primedSessionRef = useRef<string | null>(null)
  // 本次 boot 的 startSession 已落定（成功/失败）。快速 mock 下 starting 可能
  // 从未渲染为 true（setStarting 与 continuation 被批处理进同一帧），所以
  // 绑定/失败判定必须锚定 promise 落定，不能锚定 starting 状态。
  const [attemptSettled, setAttemptSettled] = useState(false)
  // 渲染面状态（lint 禁渲染期读 ref）：本次唤起绑定的会话 id 与引导失败错误。
  const [boundSessionId, setBoundSessionId] = useState<string | null>(null)
  const [bootstrapFailure, setBootstrapFailure] = useState<string | null>(null)

  // 引导：agent 列表就绪后建一次会话（picker 第一项 = 本机可用 agent）。
  const agentsReady = !chat.agentsLoading && !chat.agentsError
  useEffect(() => {
    if (!agentsReady || chat.agents.length === 0 || bootedRef.current) return
    bootedRef.current = true
    void chat
      .startSession(chat.agents[0].id)
      .then(() => setAttemptSettled(true))
      .catch(() => setAttemptSettled(true)) // startSession 内部不 reject，防御
  }, [agentsReady, chat.agents, chat])

  // 引导失败闩锁（放在捕获 effect 之前，同帧先生效）：恢复回填的会话切换
  // 会把 startSession 失败置下的 actionError 在同一批 commit 里抹掉（消息
  // 加载 effect 的 setActionError(null)）——失败必须在 wipes 之前闩住，
  // 且捕获不得读被抹后的状态。
  useEffect(() => {
    if (bootstrapFailure || !attemptSettled || boundSessionId !== null) return
    if (!chat.actionError) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 一次性引导失败闩锁（同步异步引导结果，合法 effect 用途）
    setBootstrapFailure(chat.actionError)
  }, [bootstrapFailure, attemptSettled, boundSessionId, chat.actionError])

  // 本次唤起的绑定态：boot 落定、成功窗口已绑定且当前激活的正是它。恢复
  // 先于 boot 的时序（starting 可能从未渲染为 true）骗不过 attemptSettled
  // ——落定前一律未绑定；失败时闩锁置位、永不绑定。
  const bound =
    attemptSettled &&
    boundSessionId !== null &&
    chat.activeSessionId === boundSessionId

  // 捕获 + primer 同 effect：boot 后 starting 回落、无 actionError 且 session
  // 非空 = startSession 成功落地；再等会话进入 idle（ACP 握手完成）发
  // primer——starting 状态的会话会被后端判 busy（claim idle->running）。
  // attemptSettled 闸住「恢复先于 boot」的时序：会话记忆在 startSession
  // 落定前回填的历史会话不得被捕获/收 primer；失败闩锁闸住「失败被恢复
  // 抹掉」的时序（#801 codex 轮 5 P2）。
  const sessionStatus = chat.session?.status ?? null
  useEffect(() => {
    if (!bootedRef.current || primedSessionRef.current) return
    if (!attemptSettled || bootstrapFailure) return
    if (chat.starting || chat.actionError || !chat.session) return
    const sessionId = chat.session.id
    createdSessionRef.current = sessionId
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 一次性引导绑定（同步异步引导结果，合法 effect 用途）
    setBoundSessionId(sessionId)
    if (chat.activeSessionId !== sessionId) return
    if (sessionStatus !== 'idle') return
    primedSessionRef.current = sessionId
    void chat.send(buildDiagnosisPrimer(target))
    // target 在面板生命周期内固定（一次打开对应一个 job/node）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    attemptSettled,
    bootstrapFailure,
    chat.starting,
    chat.actionError,
    chat.session,
    chat.activeSessionId,
    sessionStatus,
    chat,
  ])

  return {
    chat,
    configLocked: !bound,
    bootstrapError:
      bootstrapFailure ?? (chat.session ? null : chat.actionError),
    retryBootstrap: () => {
      bootedRef.current = false
      createdSessionRef.current = null
      primedSessionRef.current = null
      setAttemptSettled(false)
      setBoundSessionId(null)
      setBootstrapFailure(null)
    },
  }
}
