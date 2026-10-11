import { useCallback, useEffect, useRef, useState } from 'react'

/** 发送在途追踪：busy 由 SSE 会话快照驱动，首个 send 发出到快照抵达之间
 * busy 仍是 false；这期间必须自行把会话视为忙，否则快速连发的第二条也走
 * 直发路径，撞后端单 turn 原子认领的 409，且输入框已清空（消息丢失）。
 * inFlightRef 在 send 的 promise 落定后复位，供调用方把「在途」并入忙判
 * 定。独立成文件是体积预算拆分（useStudioChatQueue 无余量）。
 * #1121：sendInFlight 返回 send 的 promise（composer 据此决定清稿/留稿）；
 * 在途超过 slowAfterMs（典型为后端自动恢复会话、重建 runtime）置 slowSend，
 * 状态条据此显示「正在恢复对话…」，落定即复位。 */
export function useInFlightSend(
  send: (text: string) => Promise<boolean>,
  slowAfterMs = 2000
) {
  const sendRef = useRef(send)
  useEffect(() => {
    sendRef.current = send
  })
  const inFlightRef = useRef(false)
  const [slowSend, setSlowSend] = useState(false)
  // 'fired' 哨兵区分「定时器已触发（slowSend=true，落定需复位）」与「落定
  // 早于触发（slowSend 本就 false，同值 setState 也要省掉）」。
  const slowTimerRef = useRef<ReturnType<typeof setTimeout> | 'fired' | null>(
    null
  )
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
        const slow = slowTimerRef.current
        if (slow !== null && slow !== 'fired') clearTimeout(slow)
        slowTimerRef.current = null
        // 落定晚于卸载时 setSlowSend 是 React 18 的静默 no-op（同 ref 复位）。
        if (slow === 'fired') setSlowSend(false)
        onSent?.(sent)
      })
      return promise
    },
    [slowAfterMs]
  )
  return { inFlightRef, sendInFlight, slowSend }
}

/** #1121：发送失败恢复 composer 草稿（成功由 composer 乐观清空）——在途期间
 * 新输入的文字保留，失败的文本按时间序排在其前，什么都不丢。 */
export function restoreDraftText(
  setText: (updater: (current: string) => string) => void,
  sent: string,
  ok: boolean
) {
  if (!ok) setText((current) => (current.trim() ? `${sent}\n${current}` : sent))
}
