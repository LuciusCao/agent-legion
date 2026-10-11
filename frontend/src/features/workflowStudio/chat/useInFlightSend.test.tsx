import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { restoreDraftText, useInFlightSend } from './useInFlightSend'

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
    const { result } = renderHook(() => useInFlightSend(send, 1000))
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
    const { result } = renderHook(() => useInFlightSend(send, 1000))
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
    const { result } = renderHook(() => useInFlightSend(send, 1000))
    let settled: boolean | undefined
    await act(async () => {
      settled = await result.current.sendInFlight('hi', onSent)
    })
    expect(settled).toBe(false)
    expect(onSent).toHaveBeenCalledWith(false)
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
