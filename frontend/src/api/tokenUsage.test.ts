import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchWorkspaceTokenUsage } from './tokenUsage'

const originalFetch = global.fetch

afterEach(() => {
  global.fetch = originalFetch
  vi.restoreAllMocks()
})

function mockFetchJson(response: unknown) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve(response),
    text: () => Promise.resolve(JSON.stringify(response)),
  } as Response)
}

describe('token usage api', () => {
  it('fetches workspace token usage without a query by default', async () => {
    const payload = { groups: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await fetchWorkspaceTokenUsage('ws 1')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws%201/token-usage',
      expect.anything()
    )
  })

  it('appends caller-provided query params', async () => {
    const fetchMock = mockFetchJson({ groups: [] })
    global.fetch = fetchMock

    await fetchWorkspaceTokenUsage(
      'ws1',
      new URLSearchParams({ from: '2026-08-01' })
    )

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/token-usage?from=2026-08-01',
      expect.anything()
    )
  })
})
