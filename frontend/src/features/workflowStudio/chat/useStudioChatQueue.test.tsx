import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useStudioChatQueue } from './useStudioChatQueue'

type HookProps = { busy: boolean; sessionKey: string | null; blocked?: boolean }

function renderQueue(
  send: (text: string) => Promise<boolean>,
  initial: HookProps
) {
  return renderHook(
    (props: HookProps) =>
      useStudioChatQueue(
        props.busy,
        props.blocked ?? false,
        props.sessionKey,
        send
      ),
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

  it('holds the head while blocked and auto-flushes on the unblock edge (#694 review P2-a)', async () => {
    // 压缩开始晚于 busy 结束：busy 翻转沿被 blocked 门控压住，队首不发；
    // 压缩完成（或后端超时自清）把 blocked 翻回 false 时队首自动发出。
    const send = vi.fn().mockResolvedValue(true)
    const { result, rerender } = renderQueue(send, {
      busy: true,
      blocked: false,
      sessionKey: 's1',
    })
    act(() => result.current.submit('排队消息'))
    rerender({ busy: false, blocked: true, sessionKey: 's1' })
    await act(async () => {
      await Promise.resolve()
    })
    expect(send).not.toHaveBeenCalled()
    expect(result.current.queuedMessages.map((m) => m.text)).toEqual([
      '排队消息',
    ])

    rerender({ busy: false, blocked: false, sessionKey: 's1' })
    await waitFor(() => expect(send).toHaveBeenCalledWith('排队消息'))
    await waitFor(() => expect(result.current.queuedMessages).toEqual([]))
  })

  it('enqueues a submit made while blocked instead of direct-sending', () => {
    const send = vi.fn().mockResolvedValue(true)
    const { result } = renderQueue(send, {
      busy: false,
      blocked: true,
      sessionKey: 's1',
    })
    act(() => result.current.submit('压缩中提交'))
    expect(send).not.toHaveBeenCalled()
    expect(result.current.queuedMessages.map((m) => m.text)).toEqual([
      '压缩中提交',
    ])
  })

  it('does not spin after a flush failed inside the compaction window', async () => {
    // 409 保留队首但不触发重试：只有下一次门控翻转沿才会重发，不会空转。
    const send = vi.fn().mockResolvedValue(false)
    const { result, rerender } = renderQueue(send, {
      busy: true,
      blocked: false,
      sessionKey: 's1',
    })
    act(() => result.current.submit('第一条'))
    rerender({ busy: false, blocked: false, sessionKey: 's1' })
    await waitFor(() => expect(send).toHaveBeenCalledWith('第一条'))
    await act(async () => {
      await Promise.resolve()
    })
    // 同帧重复渲染（blocked 不变）不再重发。
    rerender({ busy: false, blocked: false, sessionKey: 's1' })
    await act(async () => {
      await Promise.resolve()
    })
    expect(send).toHaveBeenCalledTimes(1)
    expect(result.current.queuedMessages.map((m) => m.text)).toEqual(['第一条'])
    // blocked 翻转沿（压缩开始又结束）才重发同一队首，且只发一次。
    rerender({ busy: false, blocked: true, sessionKey: 's1' })
    rerender({ busy: false, blocked: false, sessionKey: 's1' })
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2))
    expect(send).toHaveBeenLastCalledWith('第一条')
  })
})
