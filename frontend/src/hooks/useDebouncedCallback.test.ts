import { renderHook, act, waitFor } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { useDebouncedCallback } from './useDebouncedCallback'

describe('useDebouncedCallback', () => {
  it('delays invocation until after delay', async () => {
    const fn = vi.fn()
    const { result } = renderHook(() => useDebouncedCallback(fn, 50))
    act(() => result.current('a'))
    act(() => result.current('b'))
    act(() => result.current('c'))
    expect(fn).not.toHaveBeenCalled()
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(1))
    expect(fn).toHaveBeenCalledWith('c')
  })

  it('cancels previous timer on rapid calls', async () => {
    const fn = vi.fn()
    const { result } = renderHook(() => useDebouncedCallback(fn, 50))
    act(() => result.current(1))
    act(() => result.current(2))
    await waitFor(() => expect(fn).toHaveBeenCalledTimes(1))
    expect(fn).toHaveBeenCalledWith(2)
  })
})
