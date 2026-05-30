import { describe, expect, it, vi } from 'vitest'

import { api } from './api'

function mockFetch(response: { ok: boolean; status: number; text: string }) {
  return vi.fn().mockResolvedValue({
    ok: response.ok,
    status: response.status,
    text: () => Promise.resolve(response.text),
    json: () => Promise.resolve(JSON.parse(response.text)),
  } as Response)
}

describe('api error handling', () => {
  it('throws detail from JSON error response', async () => {
    global.fetch = mockFetch({ ok: false, status: 400, text: JSON.stringify({ detail: 'Bad request' }) })
    await expect(api('/test')).rejects.toThrow('Bad request')
  })

  it('throws message from JSON error response when detail is absent', async () => {
    global.fetch = mockFetch({ ok: false, status: 500, text: JSON.stringify({ message: 'Server error' }) })
    await expect(api('/test')).rejects.toThrow('Server error')
  })

  it('throws HTTP status for JSON error without detail or message', async () => {
    global.fetch = mockFetch({ ok: false, status: 403, text: JSON.stringify({ error: 'Forbidden' }) })
    await expect(api('/test')).rejects.toThrow('HTTP 403')
  })

  it('truncates HTML error to 200 chars', async () => {
    const html = '<html>'.repeat(100)
    global.fetch = mockFetch({ ok: false, status: 502, text: html })
    await expect(api('/test')).rejects.toThrow(`HTTP 502: ${html.slice(0, 200)}`)
  })
})
