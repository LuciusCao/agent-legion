import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './core'
import {
  DEFAULT_READ_TIMEOUT_MS,
  DEFAULT_WRITE_TIMEOUT_MS,
  isRequestTimeoutError,
  REQUEST_TIMEOUT_CODE,
} from './requestTimeout'
import { toErrorMessage } from '../lib/queryError'

const originalFetch = global.fetch

/** 模拟僵死后端：永不响应，只在 signal abort 时以 AbortError 拒绝。 */
function hangingFetch() {
  return vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
    return new Promise<Response>((_resolve, reject) => {
      const signal = init?.signal
      if (signal?.aborted) {
        reject(new DOMException('The operation was aborted.', 'AbortError'))
        return
      }
      signal?.addEventListener('abort', () => {
        reject(new DOMException('The operation was aborted.', 'AbortError'))
      })
    })
  })
}

function okFetch(body: unknown = { ok: true }) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body),
  } as Response)
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  global.fetch = originalFetch
  vi.restoreAllMocks()
})

describe('api() default timeout (#719)', () => {
  it('fails a hanging GET with a structured timeout error after the read default', async () => {
    global.fetch = hangingFetch()
    const pending = api('/api/workspaces/ws1/stats')
    const assertion = expect(pending).rejects.toMatchObject({
      code: REQUEST_TIMEOUT_CODE,
      timeoutMs: DEFAULT_READ_TIMEOUT_MS,
    })
    await vi.advanceTimersByTimeAsync(DEFAULT_READ_TIMEOUT_MS - 1)
    await vi.advanceTimersByTimeAsync(1)
    await assertion
  })

  it('maps the timeout error through the error-mapping layer', async () => {
    global.fetch = hangingFetch()
    const pending = api('/api/jobs/j1').catch((err: unknown) => err)
    await vi.advanceTimersByTimeAsync(DEFAULT_READ_TIMEOUT_MS)
    const err = await pending
    expect(isRequestTimeoutError(err)).toBe(true)
    expect(toErrorMessage(err)).toBe('请求超时：服务端 30 秒未响应，请稍后重试')
  })

  it('gives writes the longer default', async () => {
    global.fetch = hangingFetch()
    let settled = false
    const pending = api('/api/x', { method: 'POST', body: '{}' }).catch(
      (err: unknown) => {
        settled = true
        return err
      }
    )
    await vi.advanceTimersByTimeAsync(DEFAULT_READ_TIMEOUT_MS)
    expect(settled).toBe(false)
    await vi.advanceTimersByTimeAsync(
      DEFAULT_WRITE_TIMEOUT_MS - DEFAULT_READ_TIMEOUT_MS
    )
    expect(isRequestTimeoutError(await pending)).toBe(true)
  })

  it('honours a per-call timeout override', async () => {
    global.fetch = hangingFetch()
    const pending = api('/api/x', { timeoutMs: 500 }).catch((e: unknown) => e)
    await vi.advanceTimersByTimeAsync(500)
    expect(await pending).toMatchObject({ timeoutMs: 500 })
  })

  it('disables the timeout with timeoutMs: null (long-running endpoints)', async () => {
    global.fetch = hangingFetch()
    let settled = false
    void api('/api/x', { method: 'POST', timeoutMs: null }).catch(() => {
      settled = true
    })
    await vi.advanceTimersByTimeAsync(DEFAULT_WRITE_TIMEOUT_MS * 10)
    expect(settled).toBe(false)
  })

  it('clears the timer once the response arrives', async () => {
    global.fetch = okFetch({ value: 1 })
    await expect(api('/api/x')).resolves.toEqual({ value: 1 })
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('api() AbortSignal passthrough (#719)', () => {
  it('aborts the in-flight fetch when the caller signal aborts (query cancel/unmount)', async () => {
    const fetchMock = hangingFetch()
    global.fetch = fetchMock
    const controller = new AbortController()
    const pending = api('/api/jobs/j1', { signal: controller.signal }).catch(
      (err: unknown) => err
    )
    controller.abort()
    const err = await pending
    expect((err as Error).name).toBe('AbortError')
    // 调用方取消不是超时：不走超时错误映射。
    expect(isRequestTimeoutError(err)).toBe(false)
    const passed = fetchMock.mock.calls[0][1]?.signal as AbortSignal
    expect(passed.aborted).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('rejects immediately when the caller signal is already aborted', async () => {
    const fetchMock = hangingFetch()
    global.fetch = fetchMock
    const controller = new AbortController()
    controller.abort()
    await expect(
      api('/api/x', { signal: controller.signal })
    ).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('does not forward timeoutMs to fetch', async () => {
    const fetchMock = okFetch()
    global.fetch = fetchMock
    await api('/api/x', { timeoutMs: 1000 })
    expect(fetchMock.mock.calls[0][1]).not.toHaveProperty('timeoutMs')
  })
})
