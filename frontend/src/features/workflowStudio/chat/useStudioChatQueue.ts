import { useEffect, useRef, useState } from 'react'
import { useInFlightSend } from './useInFlightSend'

export type StudioChatQueuedMessage = { id: string; text: string }

/** busy（starting/running/awaiting_permission）时的发送排队：后端 turn 是
 * 单 turn 原子认领，运行中 send 会被 409 拒绝，所以排队只能做在前端。
 * busy 由会话状态快照驱动（同 runTiming 的模式），由 true 翻转为 false 时
 * 按 FIFO 发出队首；发送失败保留队列，错误经 send 内部的 actionError 呈现。
 * 首个直发在 promise 落定前 busy 仍未翻转，这段窗口由 useInFlightSend 的
 * inFlightRef 并入忙判定，连发的后续提交一律入队。
 * blocked（#694 压缩窗口）与 busy 同等参与门控：压缩开始晚于队首发出时
 * 后端 409 保留队首，压缩结束（或后端超时自清）把 blocked 翻回 false 的
 * 翻转沿自动重发队首（#694 review P2-a）——重试只发生在门控翻转沿，
 * 失败本身不触发重试，不会空转或重复发送。
 * 不做 steer（运行中注入当前 turn）：turn 原子认领模型下运行中注入需要
 * 协议层改造，超出前端排队范围。 */
export function useStudioChatQueue(
  busy: boolean,
  blocked: boolean,
  sessionKey: string | null,
  send: (text: string) => Promise<boolean>
) {
  const [queue, setQueue] = useState<StudioChatQueuedMessage[]>([])
  const queueRef = useRef(queue)
  useEffect(() => {
    queueRef.current = queue
  }, [queue])
  const { inFlightRef, sendInFlight } = useInFlightSend(send)

  // 跨会话切换清空队列：排队消息只属于当时那个会话。
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 会话切换时重置队列（与消息列表重置同一模式）
    setQueue([])
  }, [sessionKey])

  // 只盯门控（busy || blocked）的 true→false 翻转沿 flush 队首：发送成功到
  // SSE 状态快照抵达之间有窗口，若盯 queue 变化连发会撞后端单 turn 原子
  // 认领的 409。
  const wasGatedRef = useRef(false)
  const prevKeyRef = useRef(sessionKey)
  useEffect(() => {
    const gated = busy || blocked
    const wasGated = wasGatedRef.current
    wasGatedRef.current = gated
    const switched = prevKeyRef.current !== sessionKey
    prevKeyRef.current = sessionKey
    // 同一次渲染里会话切换也可能带 busy 翻转：旧会话的队首不得发进新会话。
    if (switched || !wasGated || gated) return
    const head = queueRef.current[0]
    // 在途发送未落定时不抢发队首（等它落定、门控翻转沿再来）。
    if (!head || inFlightRef.current) return
    sendInFlight(head.text, (sent) => {
      // 失败保留队列（错误已由 send 置 actionError）；成功按 id 移除而不是
      // shift——flush 在途期间用户新入队的消息不会被误删（updater 保持纯净，
      // 无副作用，StrictMode 双调用安全）。
      if (!sent) return
      setQueue((current) => current.filter((item) => item.id !== head.id))
    })
  }, [busy, blocked, sessionKey, inFlightRef, sendInFlight])

  const nextIdRef = useRef(1)
  function submit(text: string) {
    // 首个直发在途（busy 尚未随 SSE 快照翻转）也视为忙：后续提交入队，
    // 避免两条都直发撞后端单 turn 原子认领的 409。压缩窗口（blocked）同理。
    if (busy || blocked || inFlightRef.current) {
      const id = `q${nextIdRef.current}`
      nextIdRef.current += 1
      setQueue((current) => [...current, { id, text }])
      return
    }
    sendInFlight(text)
  }

  function remove(id: string) {
    setQueue((current) => current.filter((item) => item.id !== id))
  }

  return { queuedMessages: queue, submit, remove }
}
