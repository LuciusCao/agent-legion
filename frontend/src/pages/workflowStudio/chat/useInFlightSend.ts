import { useCallback, useEffect, useRef } from 'react'

/** 发送在途追踪：busy 由 SSE 会话快照驱动，首个 send 发出到快照抵达之间
 * busy 仍是 false；这期间必须自行把会话视为忙，否则快速连发的第二条也走
 * 直发路径，撞后端单 turn 原子认领的 409，且输入框已清空（消息丢失）。
 * inFlightRef 在 send 的 promise 落定后复位，供调用方把「在途」并入忙判
 * 定。独立成文件是体积预算拆分（useStudioChatQueue 无余量）。 */
export function useInFlightSend(send: (text: string) => Promise<boolean>) {
  const sendRef = useRef(send)
  useEffect(() => {
    sendRef.current = send
  })
  const inFlightRef = useRef(false)
  const sendInFlight = useCallback(
    (text: string, onSent?: (sent: boolean) => void) => {
      inFlightRef.current = true
      void sendRef.current(text).then((sent) => {
        inFlightRef.current = false
        onSent?.(sent)
      })
    },
    []
  )
  return { inFlightRef, sendInFlight }
}
