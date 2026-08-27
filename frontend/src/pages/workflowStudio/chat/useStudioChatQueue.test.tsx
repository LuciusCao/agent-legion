import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useStudioChatQueue } from './useStudioChatQueue'

type HookProps = { busy: boolean; sessionKey: string | null }

function renderQueue(
  send: (text: string) => Promise<boolean>,
  initial: HookProps
) {
  return renderHook(
    (props: HookProps) =>
      useStudioChatQueue(props.busy, props.sessionKey, send),
    { initialProps: initial }
  )
}

describe('useStudioChatQueue', () => {
  it('sends immediately when idle', () => {
    const send = vi.fn().mockResolvedValue(true)
    const { result } = renderQueue(send, { busy: false, sessionKey: 's1' })
    act(() => result.current.submit('你好'))
    expect(send).toHaveBeenCalledWith('你好')
    expect(result.current.queuedMessages).toEqual([])
  })

  it('enqueues while busy without sending', () => {
    const send = vi.fn().mockResolvedValue(true)
    const { result } = renderQueue(send, { busy: true, sessionKey: 's1' })
    act(() => result.current.submit('第一条'))
    act(() => result.current.submit('第二条'))
    expect(send).not.toHaveBeenCalled()
    expect(result.current.queuedMessages.map((m) => m.text)).toEqual([
      '第一条',
      '第二条',
    ])
  })

  it('flushes the queue head FIFO on each busy-to-idle flip', async () => {
    const send = vi.fn().mockResolvedValue(true)
    const { result, rerender } = renderQueue(send, {
      busy: true,
      sessionKey: 's1',
    })
    act(() => result.current.submit('第一条'))
    act(() => result.current.submit('第二条'))

    rerender({ busy: false, sessionKey: 's1' })
    await waitFor(() => expect(send).toHaveBeenCalledWith('第一条'))
    await waitFor(() =>
      expect(result.current.queuedMessages.map((m) => m.text)).toEqual([
        '第二条',
      ])
    )
    // 发送成功到 SSE 状态快照抵达之间有窗口：不盯 queue 变化连发。
    expect(send).toHaveBeenCalledTimes(1)

    rerender({ busy: true, sessionKey: 's1' })
    rerender({ busy: false, sessionKey: 's1' })
    await waitFor(() => expect(send).toHaveBeenCalledWith('第二条'))
    await waitFor(() => expect(result.current.queuedMessages).toEqual([]))
  })

  it('keeps the queue when the flush send fails', async () => {
    const send = vi.fn().mockResolvedValue(false)
    const { result, rerender } = renderQueue(send, {
      busy: true,
      sessionKey: 's1',
    })
    act(() => result.current.submit('第一条'))
    rerender({ busy: false, sessionKey: 's1' })
    await waitFor(() => expect(send).toHaveBeenCalledWith('第一条'))
    // 等 then 回调落定后断言队列未被清空。
    await act(async () => {
      await Promise.resolve()
    })
    expect(result.current.queuedMessages.map((m) => m.text)).toEqual(['第一条'])
  })

  it('serializes rapid submits while the first send is in flight', async () => {
    // 首个 send 在途、busy 尚未随 SSE 快照翻转时，第二条必须入队而不是
    // 直发（否则两条都撞后端单 turn 原子认领，第二条 409 且输入已清空）。
    let resolveSend: (sent: boolean) => void = () => {}
    const send = vi.fn().mockImplementation(
      () =>
        new Promise<boolean>((resolve) => {
          resolveSend = resolve
        })
    )
    const { result, rerender } = renderQueue(send, {
      busy: false,
      sessionKey: 's1',
    })
    act(() => result.current.submit('第一条'))
    expect(send).toHaveBeenCalledWith('第一条')

    act(() => result.current.submit('第二条'))
    expect(send).toHaveBeenCalledTimes(1)
    expect(result.current.queuedMessages.map((m) => m.text)).toEqual(['第二条'])

    // 在途发送落定 + busy 翻转沿 → 队首发出。
    await act(async () => {
      resolveSend(true)
    })
    rerender({ busy: true, sessionKey: 's1' })
    rerender({ busy: false, sessionKey: 's1' })
    await waitFor(() => expect(send).toHaveBeenCalledWith('第二条'))
    await act(async () => {
      resolveSend(true)
    })
    await waitFor(() => expect(result.current.queuedMessages).toEqual([]))
  })

  it('queues a submit that arrives while the queue-head flush is in flight', async () => {
    // flush 发出队首的 promise 未落定时新提交的消息：入队而不是直发。
    let resolveSend: (sent: boolean) => void = () => {}
    const send = vi.fn().mockImplementation(
      () =>
        new Promise<boolean>((resolve) => {
          resolveSend = resolve
        })
    )
    const { result, rerender } = renderQueue(send, {
      busy: true,
      sessionKey: 's1',
    })
    act(() => result.current.submit('第一条'))
    rerender({ busy: false, sessionKey: 's1' })
    await waitFor(() => expect(send).toHaveBeenCalledWith('第一条'))

    act(() => result.current.submit('第二条'))
    expect(send).toHaveBeenCalledTimes(1)
    expect(result.current.queuedMessages.map((m) => m.text)).toEqual([
      '第一条',
      '第二条',
    ])

    await act(async () => {
      resolveSend(true)
    })
    await waitFor(() =>
      expect(result.current.queuedMessages.map((m) => m.text)).toEqual([
        '第二条',
      ])
    )
  })

  it('removes a queued message manually', () => {
    const send = vi.fn().mockResolvedValue(true)
    const { result } = renderQueue(send, { busy: true, sessionKey: 's1' })
    act(() => result.current.submit('第一条'))
    act(() => result.current.submit('第二条'))
    const head = result.current.queuedMessages[0]
    act(() => result.current.remove(head.id))
    expect(result.current.queuedMessages.map((m) => m.text)).toEqual(['第二条'])
  })

  it('clears the queue on session switch', () => {
    const send = vi.fn().mockResolvedValue(true)
    const { result, rerender } = renderQueue(send, {
      busy: true,
      sessionKey: 's1',
    })
    act(() => result.current.submit('第一条'))
    rerender({ busy: true, sessionKey: 's2' })
    expect(result.current.queuedMessages).toEqual([])
  })

  it('does not flush the old session queue into a new session', async () => {
    const send = vi.fn().mockResolvedValue(true)
    const { result, rerender } = renderQueue(send, {
      busy: true,
      sessionKey: 's1',
    })
    act(() => result.current.submit('第一条'))
    // 切到空闲的新会话：busy 翻转与会话切换同帧，旧队首不得发出。
    rerender({ busy: false, sessionKey: 's2' })
    await act(async () => {
      await Promise.resolve()
    })
    expect(send).not.toHaveBeenCalled()
  })
})
