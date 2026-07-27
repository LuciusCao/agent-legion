/// <reference types="node" />
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from './core'
import { setUnauthorizedHandler } from './requestAuth'

const originalFetch = global.fetch

afterEach(() => {
  global.fetch = originalFetch
  setUnauthorizedHandler(null)
  vi.restoreAllMocks()
  window.history.pushState({}, '', '/')
})

function mockFetchResponse(init: {
  ok: boolean
  status: number
  body?: unknown
}) {
  const text = JSON.stringify(init.body ?? {})
  return vi.fn().mockResolvedValue({
    ok: init.ok,
    status: init.status,
    text: () => Promise.resolve(text),
    json: () => Promise.resolve(JSON.parse(text)),
  } as Response)
}

function requestHeaders(fetchMock: ReturnType<typeof mockFetchResponse>) {
  return (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<
    string,
    string
  >
}

describe('api CSRF header', () => {
  it('injects x-agent-legion-request on POST', async () => {
    const fetchMock = mockFetchResponse({ ok: true, status: 200 })
    global.fetch = fetchMock

    await api('/api/test', { method: 'POST', body: '{}' })

    expect(requestHeaders(fetchMock)['x-agent-legion-request']).toBe('1')
  })

  it('injects x-agent-legion-request on PUT, PATCH and DELETE', async () => {
    for (const method of ['PUT', 'PATCH', 'DELETE']) {
      const fetchMock = mockFetchResponse({ ok: true, status: 200 })
      global.fetch = fetchMock

      await api('/api/test', { method, body: '{}' })

      expect(requestHeaders(fetchMock)['x-agent-legion-request']).toBe('1')
    }
  })

  it('does not inject x-agent-legion-request on GET', async () => {
    const fetchMock = mockFetchResponse({ ok: true, status: 200 })
    global.fetch = fetchMock

    await api('/api/test')

    expect(requestHeaders(fetchMock)['x-agent-legion-request']).toBeUndefined()
  })
})

describe('api 401 handling', () => {
  it('invokes the unauthorized handler for non-auth paths', async () => {
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    global.fetch = mockFetchResponse({
      ok: false,
      status: 401,
      body: { detail: 'Not authenticated' },
    })

    await expect(api('/api/workspaces')).rejects.toThrow('Not authenticated')
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('skips the handler for /api/auth/ requests', async () => {
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    global.fetch = mockFetchResponse({
      ok: false,
      status: 401,
      body: { detail: 'Invalid username or password' },
    })

    await expect(
      api('/api/auth/login', { method: 'POST', body: '{}' })
    ).rejects.toThrow('Invalid username or password')
    expect(handler).not.toHaveBeenCalled()
  })

  it('skips the handler when already on /login', async () => {
    window.history.pushState({}, '', '/login')
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    global.fetch = mockFetchResponse({
      ok: false,
      status: 401,
      body: { detail: 'Not authenticated' },
    })

    await expect(api('/api/workspaces')).rejects.toThrow('Not authenticated')
    expect(handler).not.toHaveBeenCalled()
  })

  it('still throws the 401 error after invoking the handler', async () => {
    setUnauthorizedHandler(vi.fn())
    global.fetch = mockFetchResponse({
      ok: false,
      status: 401,
      body: { detail: 'Not authenticated' },
    })

    const error = await api('/api/workspaces').catch((err: unknown) => err)
    expect((error as { status?: number }).status).toBe(401)
  })
})
