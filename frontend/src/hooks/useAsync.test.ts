import { describe, it, expect, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useAsync } from './useAsync'

describe('useAsync', () => {
  it('starts loading and resolves data on success', async () => {
    const task = vi.fn().mockResolvedValue('value')

    const { result } = renderHook(() => useAsync(task, ['key']))

    expect(result.current.loading).toBe(true)
    expect(result.current.data).toBeNull()
    expect(result.current.error).toBe('')

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toBe('value')
    expect(result.current.error).toBe('')
  })

  it('sets error message and clears data when the task rejects', async () => {
    const task = vi.fn().mockRejectedValue(new Error('boom'))

    const { result } = renderHook(() => useAsync(task, ['key']))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toBeNull()
    expect(result.current.error).toBe('boom')
  })

  it('stringifies non-Error rejections', async () => {
    const task = vi.fn().mockRejectedValue('plain failure')

    const { result } = renderHook(() => useAsync(task, ['key']))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('plain failure')
  })

  it('captures synchronous throws from the task', async () => {
    const task = vi.fn(() => {
      throw new Error('sync boom')
    })

    const { result } = renderHook(() => useAsync(task, ['key']))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('sync boom')
  })

  it('clears the previous error when a re-run succeeds', async () => {
    const task = vi
      .fn()
      .mockRejectedValueOnce(new Error('first failed'))
      .mockResolvedValueOnce('recovered')

    const { result, rerender } = renderHook(
      ({ dep }) => useAsync(task, [dep]),
      { initialProps: { dep: 'a' } }
    )

    await waitFor(() => expect(result.current.error).toBe('first failed'))

    rerender({ dep: 'b' })

    await waitFor(() => expect(result.current.data).toBe('recovered'))
    expect(result.current.error).toBe('')
    expect(task).toHaveBeenCalledTimes(2)
  })

  it('keeps previous data and loading=false on re-run by default', async () => {
    let resolveSecond: (value: string) => void = () => {}
    const task = vi
      .fn()
      .mockResolvedValueOnce('first')
      .mockImplementationOnce(
        () =>
          new Promise<string>((resolve) => {
            resolveSecond = resolve
          })
      )

    const { result, rerender } = renderHook(
      ({ dep }) => useAsync(task, [dep]),
      { initialProps: { dep: 'a' } }
    )

    await waitFor(() => expect(result.current.data).toBe('first'))

    rerender({ dep: 'b' })

    expect(result.current.loading).toBe(false)
    expect(result.current.data).toBe('first')

    await waitFor(() => expect(task).toHaveBeenCalledTimes(2))
    resolveSecond('second')
    await waitFor(() => expect(result.current.data).toBe('second'))
  })

  it('resets to the pending state on re-run when resetOnRun is set', async () => {
    let resolveSecond: (value: string) => void = () => {}
    const task = vi
      .fn()
      .mockResolvedValueOnce('first')
      .mockImplementationOnce(
        () =>
          new Promise<string>((resolve) => {
            resolveSecond = resolve
          })
      )

    const { result, rerender } = renderHook(
      ({ dep }) => useAsync(task, [dep], { resetOnRun: true }),
      { initialProps: { dep: 'a' } }
    )

    await waitFor(() => expect(result.current.data).toBe('first'))

    rerender({ dep: 'b' })

    await waitFor(() => expect(result.current.loading).toBe(true))
    expect(result.current.data).toBeNull()

    resolveSecond('second')
    await waitFor(() => expect(result.current.data).toBe('second'))
    expect(result.current.loading).toBe(false)
  })

  it('stays idle while disabled and starts when enabled flips to true', async () => {
    const task = vi.fn().mockResolvedValue('value')

    const { result, rerender } = renderHook(
      ({ enabled }) => useAsync(task, ['key'], { enabled }),
      { initialProps: { enabled: false } }
    )

    expect(result.current.loading).toBe(false)
    expect(result.current.data).toBeNull()
    expect(task).not.toHaveBeenCalled()

    rerender({ enabled: true })

    await waitFor(() => expect(result.current.data).toBe('value'))
    expect(task).toHaveBeenCalledTimes(1)
  })

  it('discards results that arrive after unmount', async () => {
    let resolveTask: (value: string) => void = () => {}
    const task = vi.fn(
      () =>
        new Promise<string>((resolve) => {
          resolveTask = resolve
        })
    )

    const { result, unmount } = renderHook(() => useAsync(task, ['key']))
    unmount()
    resolveTask('late')

    expect(result.current.loading).toBe(true)
    expect(result.current.data).toBeNull()
  })

  it('discards results from a stale run after deps change', async () => {
    let resolveFirst: (value: string) => void = () => {}
    const task = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<string>((resolve) => {
            resolveFirst = resolve
          })
      )
      .mockResolvedValueOnce('second')

    const { result, rerender } = renderHook(
      ({ dep }) => useAsync(task, [dep]),
      { initialProps: { dep: 'a' } }
    )

    rerender({ dep: 'b' })
    await waitFor(() => expect(result.current.data).toBe('second'))

    resolveFirst('stale')

    // Give the stale promise a chance to settle.
    await Promise.resolve()
    expect(result.current.data).toBe('second')
  })
})
