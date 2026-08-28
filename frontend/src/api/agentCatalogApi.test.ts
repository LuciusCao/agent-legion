import { afterEach, describe, expect, it, vi } from 'vitest'

import { getSkillDetail } from './agentCatalogApi'

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

describe('agentCatalogApi', () => {
  it('fetches skill detail without a ref by default', async () => {
    const payload = { key: 'demo/review', ref: 'v1.2.0', commit: 'abc' }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await getSkillDetail('demo/review')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-catalog/skills/demo/review',
      expect.anything()
    )
  })

  it('appends the ref query when a skill version is selected', async () => {
    const payload = { key: 'demo/review', ref: 'v1.3.0', commit: 'def' }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await getSkillDetail('demo/review', 'v1.3.0')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-catalog/skills/demo/review?ref=v1.3.0',
      expect.anything()
    )
  })
})
