import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useInFlightSend } from './useInFlightSend'
import { restoreDraftText } from './restoreDraftText'

describe('useInFlightSend', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('flags a slow in-flight send and clears the flag on settle (#1121)', async () => {
    let settle: (ok: boolean) => void = () => {}
    const send = vi.fn().mockImplementation(
      () =>
        new Promise<boolean>((resolve) => {
          settle = resolve
        })
    )
    const { result } = renderHook(() => useInFlightSend(send, undefined, 1000))
    act(() => {
      void result.current.sendInFlight('hi')
    })
    expect(result.current.slowSend).toBe(false)
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(result.current.slowSend).toBe(true)
    await act(async () => {
      settle(true)
    })
    expect(result.current.slowSend).toBe(false)
    expect(result.current.inFlightRef.current).toBe(false)
  })

  it('never flags slowSend for a fast send', async () => {
    const send = vi.fn().mockResolvedValue(true)
    const { result } = renderHook(() => useInFlightSend(send, undefined, 1000))
    await act(async () => {
      await result.current.sendInFlight('hi')
    })
    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(result.current.slowSend).toBe(false)
  })

  it('sendInFlight resolves with the send result and invokes onSent', async () => {
    const send = vi.fn().mockResolvedValue(false)
    const onSent = vi.fn()
    const { result } = renderHook(() => useInFlightSend(send, undefined, 1000))
    let settled: boolean | undefined
    await act(async () => {
      settled = await result.current.sendInFlight('hi', onSent)
    })
    expect(settled).toBe(false)
    expect(onSent).toHaveBeenCalledWith(false)
  })

  it('cancels the pending slowSend timer when the resetKey changes (#1238 codex 四轮 P2-2)', () => {
    const send = vi
      .fn()
      .mockImplementation(() => new Promise<boolean>(() => {}))
    const { result, rerender } = renderHook(
      ({ sessionKey }: { sessionKey: string }) =>
        useInFlightSend(send, sessionKey, 1000),
      { initialProps: { sessionKey: 's1' } }
    )
    act(() => {
      void result.current.sendInFlight('hi')
    })
    // 会话切换（commit 阶段取消旧定时器）；旧请求的定时器之后不得再触发。
    rerender({ sessionKey: 's2' })
    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(result.current.slowSend).toBe(false)
  })

  it('resets an already-fired slowSend when the resetKey changes (#1238 codex 四轮 P2-2)', () => {
    const send = vi
      .fn()
      .mockImplementation(() => new Promise<boolean>(() => {}))
    const { result, rerender } = renderHook(
      ({ sessionKey }: { sessionKey: string }) =>
        useInFlightSend(send, sessionKey, 1000),
      { initialProps: { sessionKey: 's1' } }
    )
    act(() => {
      void result.current.sendInFlight('hi')
    })
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(result.current.slowSend).toBe(true)
    rerender({ sessionKey: 's2' })
    expect(result.current.slowSend).toBe(false)
  })

  it('frees the in-flight marker for the new session on resetKey change (#1238 codex 五轮)', () => {
    // 旧会话直发永不落定：切会话后 inFlightRef 必须复位——否则新会话的
    // 直发被 queue 误判为「旧请求仍占用」而仅入队，且旧请求落定只改 ref
    // 不触发渲染，flush 又只认 busy 翻转沿，新消息滞留队列永不发出。
    const send = vi
      .fn()
      .mockImplementation(() => new Promise<boolean>(() => {}))
    const { result, rerender } = renderHook(
      ({ sessionKey }: { sessionKey: string }) =>
        useInFlightSend(send, sessionKey, 1000),
      { initialProps: { sessionKey: 's1' } }
    )
    act(() => {
      void result.current.sendInFlight('hi')
    })
    expect(result.current.inFlightRef.current).toBe(true)
    rerender({ sessionKey: 's2' })
    expect(result.current.inFlightRef.current).toBe(false)
  })

  it('a late settle from the old generation must not clobber the new one (#1238 codex 五轮)', async () => {
    // 旧代次（s1）发送在途时切到 s2 并发出新发送：旧请求的迟到落定不得
    // 清掉新代次的 in-flight 标记与慢发送定时器。
    let settleOld: (ok: boolean) => void = () => {}
    let settleNew: (ok: boolean) => void = () => {}
    const send = vi
      .fn()
      .mockImplementationOnce(
        () => new Promise<boolean>((resolve) => (settleOld = resolve))
      )
      .mockImplementationOnce(
        () => new Promise<boolean>((resolve) => (settleNew = resolve))
      )
    const { result, rerender } = renderHook(
      ({ sessionKey }: { sessionKey: string }) =>
        useInFlightSend(send, sessionKey, 1000),
      { initialProps: { sessionKey: 's1' } }
    )
    act(() => {
      void result.current.sendInFlight('old')
    })
    rerender({ sessionKey: 's2' })
    act(() => {
      void result.current.sendInFlight('new')
    })
    expect(result.current.inFlightRef.current).toBe(true)
    // 旧代次迟到落定：新代次状态不受影响。
    await act(async () => {
      settleOld(true)
    })
    expect(result.current.inFlightRef.current).toBe(true)
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(result.current.slowSend).toBe(true) // 新代次的慢定时器未被旧落定清掉
    // 新代次落定：正常复位。
    await act(async () => {
      settleNew(true)
    })
    expect(result.current.inFlightRef.current).toBe(false)
    expect(result.current.slowSend).toBe(false)
  })
})

describe('restoreDraftText (#1121)', () => {
  it('restores the failed text into an empty box', () => {
    const setText = vi.fn()
    restoreDraftText(setText, '发失败的消息', false)
    const updater = setText.mock.calls[0][0]
    expect(updater('')).toBe('发失败的消息')
  })

  it('prepends the failed text ahead of newer in-flight typing', () => {
    const setText = vi.fn()
    restoreDraftText(setText, '先发', false)
    const updater = setText.mock.calls[0][0]
    expect(updater('后打')).toBe('先发\n后打')
  })

  it('does nothing on success', () => {
    const setText = vi.fn()
    restoreDraftText(setText, '已送达', true)
    expect(setText).not.toHaveBeenCalled()
  })
})
