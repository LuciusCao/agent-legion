import { useCallback, useLayoutEffect, useRef, useState } from 'react'

type SlowTimer = ReturnType<typeof setTimeout> | 'fired' | null

/** 发送在途追踪：busy 由 SSE 会话快照驱动，首个 send 发出到快照抵达之间
 * busy 仍是 false；这期间必须自行把会话视为忙，否则快速连发的第二条也走
 * 直发路径，撞后端单 turn 原子认领的 409，且输入框已清空（消息丢失）。
 * inFlightRef 在 send 的 promise 落定后复位，供调用方把「在途」并入忙判
 * 定。独立成文件是体积预算拆分（useStudioChatQueue 无余量）。
 * #1121：sendInFlight 返回 send 的 promise（composer 据此决定清稿/留稿）；
 * 在途超过 slowAfterMs（典型为后端自动恢复会话、重建 runtime）置 slowSend，
 * 状态条据此显示「正在恢复对话…」，落定即复位。
 * #1238 codex 四轮 P2-2：本 hook 活在共享 panel 实例上，跨会话切换不卸载——
 * resetKey（调用方传 sessionKey）变化即取消旧会话的慢发送定时器并复位，
 * 旧请求的迟到触发不得在新会话上显示「正在恢复对话…」（该提示优先于
 * 真正的 error 恢复入口，不取消会把新会话的「继续对话」藏到旧请求落定）。 */
export function useInFlightSend(
  send: (text: string) => Promise<boolean>,
  resetKey?: unknown,
  slowAfterMs = 2000
) {
  const sendRef = useRef(send)
  const inFlightRef = useRef(false)
  const [slowSend, setSlowSend] = useState(false)
  // 'fired' 哨兵区分「定时器已触发（slowSend=true，落定需复位）」与「落定
  // 早于触发（slowSend 本就 false，同值 setState 也要省掉）」。
  const slowTimerRef = useRef<SlowTimer>(null)
  const clearSlow = useCallback(() => {
    const slow = slowTimerRef.current
    if (slow !== null && slow !== 'fired') clearTimeout(slow)
    slowTimerRef.current = null
    if (slow === 'fired') setSlowSend(false)
  }, [])
  useLayoutEffect(() => {
    sendRef.current = send
  })
  // 挂载即跑一次是无害 no-op（定时器未武装）；切换在 commit 阶段生效。
  useLayoutEffect(() => clearSlow(), [resetKey, clearSlow])
  const sendInFlight = useCallback(
    (text: string, onSent?: (sent: boolean) => void) => {
      inFlightRef.current = true
      // 上一棒的定时器在落定回调里必已清理（inFlight 门控保证串行），这里直接武装。
      slowTimerRef.current = setTimeout(() => {
        slowTimerRef.current = 'fired'
        setSlowSend(true)
      }, slowAfterMs)
      const promise = sendRef.current(text)
      void promise.then((sent) => {
        inFlightRef.current = false
        clearSlow()
        onSent?.(sent)
      })
      return promise
    },
    [slowAfterMs, clearSlow]
  )
  return { inFlightRef, sendInFlight, slowSend }
}
