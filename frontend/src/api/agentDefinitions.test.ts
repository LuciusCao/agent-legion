import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchAgentDefinitions, fetchAgentProvenance } from './agentDefinitions'

const originalFetch = global.fetch

const WS = 'ws1'
const WS_QUERY = '?workspace_id=ws1'

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

describe('agentDefinitions api', () => {
  it('lists agent definitions', async () => {
    const payload = { agents: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await fetchAgentDefinitions(WS)

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/agent-definitions${WS_QUERY}`,
      expect.anything()
    )
  })

  it('fetches the inlined-node provenance of the active revision', async () => {
    const payload = { nodes: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await fetchAgentProvenance('ws 1')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws%201/agent-provenance',
      expect.anything()
    )
  })
})
