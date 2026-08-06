import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchSkillTags, validateSkillPath } from './skills'

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

describe('skills api', () => {
  it('validates a skill path', async () => {
    const payload = {
      valid: true,
      path: '/abs/skill',
      skill_key: 'ns/skill',
      tags: ['v1.0.0'],
      latest_tag: 'v1.0.0',
      locked_ref: 'v1.0.0',
    }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await validateSkillPath('/abs/skill')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/skills/validate',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ path: '/abs/skill' }),
      })
    )
  })

  it('fetches skill tags with an encoded path', async () => {
    const payload = { path: '/abs/skill a', tags: [], latest_tag: null }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await fetchSkillTags('/abs/skill a')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/skills/tags?path=%2Fabs%2Fskill%20a',
      expect.anything()
    )
  })
})
