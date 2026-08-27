import { useEffect, useRef, useState } from 'react'

export type StudioChatQueuedMessage = { id: string; text: string }

/** busy（starting/running/awaiting_permission）时的发送排队：后端 turn 是
 * 单 turn 原子认领，运行中 send 会被 409 拒绝，所以排队只能做在前端。
 * busy 由会话状态快照驱动（同 runTiming 的模式），由 true 翻转为 false 时
 * 按 FIFO 发出队首；发送失败保留队列，错误经 send 内部的 actionError 呈现。
 * 不做 steer（运行中注入当前 turn）：turn 原子认领模型下运行中注入需要
 * 协议层改造，超出前端排队范围。 */
export function useStudioChatQueue(
  busy: boolean,
  sessionKey: string | null,
  send: (text: string) => Promise<boolean>
) {
  const [queue, setQueue] = useState<StudioChatQueuedMessage[]>([])
  const queueRef = useRef(queue)
  useEffect(() => {
    queueRef.current = queue
  }, [queue])
  const sendRef = useRef(send)
  useEffect(() => {
    sendRef.current = send
  })

  // 跨会话切换清空队列：排队消息只属于当时那个会话。
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 会话切换时重置队列（与消息列表重置同一模式）
    setQueue([])
  }, [sessionKey])

  // 只盯 busy 的 true→false 翻转沿 flush 队首：发送成功到 SSE 状态快照
  // 抵达之间有窗口，若盯 queue 变化连发会撞后端单 turn 原子认领的 409。
  const wasBusyRef = useRef(false)
  const prevKeyRef = useRef(sessionKey)
  useEffect(() => {
    const wasBusy = wasBusyRef.current
    wasBusyRef.current = busy
    const switched = prevKeyRef.current !== sessionKey
    prevKeyRef.current = sessionKey
    // 同一次渲染里会话切换也可能带 busy 翻转：旧会话的队首不得发进新会话。
    if (switched || !wasBusy || busy) return
    const head = queueRef.current[0]
    if (!head) return
    void sendRef.current(head.text).then((sent) => {
      // 失败保留队列（错误已由 send 置 actionError）；成功按 id 移除而不是
      // shift——flush 在途期间用户新入队的消息不会被误删（updater 保持纯净，
      // 无副作用，StrictMode 双调用安全）。
      if (!sent) return
      setQueue((current) => current.filter((item) => item.id !== head.id))
    })
  }, [busy, sessionKey])

  const nextIdRef = useRef(1)
  function submit(text: string) {
    if (!busy) {
      void sendRef.current(text)
      return
    }
    const id = `q${nextIdRef.current}`
    nextIdRef.current += 1
    setQueue((current) => [...current, { id, text }])
  }

  function remove(id: string) {
    setQueue((current) => current.filter((item) => item.id !== id))
  }

  return { queuedMessages: queue, submit, remove }
}
