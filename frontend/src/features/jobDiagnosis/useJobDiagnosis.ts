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
  /** 配置芯片未绑定本次唤起会话前锁定（#801 codex 轮 5-7）：bootstrap 在途
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
 * 绑定判定（#801 codex 轮 7 根因方案）：成败由 startSession 返回值带回
 * （attemptSettled 落定 + attemptFailed 标记）——失败闩锁只在本次尝试
 * 失败时采信 actionError，无关的历史加载错误（恢复会话消息拉取失败）
 * 遇上创建成功一律不闩（#801 codex 轮 7 P2）；本次创建真失败闩住并如实
 * 呈现（#801 codex 轮 5 P2）；引导成功后的普通发送错误不影响已绑定态。 */
export function useJobDiagnosis(
  workspaceId: string,
  target: JobDiagnosisTarget
): JobDiagnosisChat {
  const chat = useStudioChat(workspaceId)
  const bootedRef = useRef(false)
  const primedSessionRef = useRef<string | null>(null)
  // 本次 boot 的 startSession 已落定（成功/失败）。快速 mock 下 starting 可能
  // 从未渲染为 true（setStarting 与 continuation 被批处理进同一帧），所以
  // 绑定/失败判定必须锚定 promise 落定，不能锚定 starting 状态。
  const [attemptSettled, setAttemptSettled] = useState(false)
  // 渲染面状态（lint 禁渲染期读 ref）：本次尝试是否失败（startSession 返回
  // 值带回）与引导失败闩锁。
  const [attemptFailed, setAttemptFailed] = useState(false)
  const [bootstrapFailure, setBootstrapFailure] = useState<string | null>(null)

  // 引导：agent 列表就绪后建一次会话（picker 第一项 = 本机可用 agent）。
  const agentsReady = !chat.agentsLoading && !chat.agentsError
  useEffect(() => {
    if (!agentsReady || chat.agents.length === 0 || bootedRef.current) return
    bootedRef.current = true
    void chat.startSession(chat.agents[0].id).then((ok) => {
      setAttemptSettled(true)
      if (ok !== true) setAttemptFailed(true)
    })
  }, [agentsReady, chat.agents, chat])

  // 成功 = 落定且未失败且当前会话已激活（新建会话已落地）。恢复会话消息
  // 拉取失败留下的无关 actionError 不挡成功判定（轮 7 P2）。
  const successNow = attemptSettled && !attemptFailed && chat.session !== null

  // 引导失败闩锁（放在 primer effect 之前，同帧先生效）：只在没有
  // successNow 时采信 actionError——创建真失败时无新会话，错误被闩住；
  // 恢复回填把 actionError 抹掉的时序也骗不过它（闩锁先于 wipe 读帧）。
  useEffect(() => {
    if (bootstrapFailure || !attemptSettled || successNow || !chat.actionError)
      return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 一次性引导失败闩锁（同步异步引导结果，合法 effect 用途）
    setBootstrapFailure(chat.actionError)
  }, [bootstrapFailure, attemptSettled, successNow, chat])

  // primer 同 effect：落定且成功、会话进入 idle（ACP 握手完成）发 primer——
  // starting 状态的会话会被后端判 busy（claim idle->running）。
  useEffect(() => {
    if (!bootedRef.current || primedSessionRef.current) return
    if (!attemptSettled || bootstrapFailure || !successNow) return
    if (chat.starting || chat.session?.status !== 'idle') return
    primedSessionRef.current = chat.activeSessionId
    void chat.send(buildDiagnosisPrimer(target))
    // target 在面板生命周期内固定（一次打开对应一个 job/node）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attemptSettled, bootstrapFailure, successNow, chat])

  return {
    chat,
    configLocked: !successNow,
    bootstrapError:
      bootstrapFailure ?? (chat.session ? null : chat.actionError),
    retryBootstrap: () => {
      // 先清底层 actionError（#801 codex 轮 6 P2）：上一次创建失败的残留若
      // 不清，重试成功帧上它仍在，失败闩锁会误采。
      chat.clearActionError()
      bootedRef.current = false
      primedSessionRef.current = null
      setAttemptSettled(false)
      setAttemptFailed(false)
      setBootstrapFailure(null)
    },
  }
}
