import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  archiveAgent,
  copyAgent,
  createAgentDefinition,
  fetchAgentDefinition,
  fetchAgentDefinitions,
  fetchAgentVersions,
  publishAgent,
  rollbackAgent,
  saveAgentDraft,
} from './agentDefinitions'

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

describe('agentDefinitions api', () => {
  it('lists agent definitions', async () => {
    const payload = { agents: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await fetchAgentDefinitions()

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-definitions',
      expect.anything()
    )
  })

  it('fetches an agent detail with an encoded id', async () => {
    const payload = { agent_id: 'a b', latest: null, published: null }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await fetchAgentDefinition('a b')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-definitions/a%20b',
      expect.anything()
    )
  })

  it('lists agent versions', async () => {
    const payload = { versions: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await fetchAgentVersions('agent-1')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-definitions/agent-1/versions',
      expect.anything()
    )
  })

  it('creates an agent definition', async () => {
    const payload = { id: 'v1', agent_id: 'agent-1', version: 1 }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const body = {
      agent_id: 'agent-1',
      capability: 'generate_key_info',
      runtime: 'pi' as const,
      skill: 'question_comprehension_info/generate_key_info',
      tools: ['read'],
    }
    const result = await createAgentDefinition(body)

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-definitions',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(body) })
    )
  })

  it('saves a draft', async () => {
    const payload = { id: 'v2', agent_id: 'agent-1', version: 2 }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const body = {
      capability: 'generate_key_info',
      runtime: 'velites' as const,
      skill: 'some/skill',
    }
    const result = await saveAgentDraft('agent-1', body)

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-definitions/agent-1/draft',
      expect.objectContaining({ method: 'PUT', body: JSON.stringify(body) })
    )
  })

  it('publishes an agent', async () => {
    const payload = { id: 'v2', status: 'published' }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await publishAgent('agent-1')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-definitions/agent-1/publish',
      expect.objectContaining({ method: 'POST' })
    )
  })

  it('rolls back to a version', async () => {
    const payload = { id: 'v3', version: 3 }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await rollbackAgent('agent-1', 2)

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-definitions/agent-1/rollback',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ version: 2 }),
      })
    )
  })

  it('copies an agent', async () => {
    const payload = { id: 'v1', agent_id: 'agent-2' }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await copyAgent('agent-1', 'agent-2')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-definitions/agent-1/copy',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ new_agent_id: 'agent-2' }),
      })
    )
  })

  it('archives an agent', async () => {
    const payload = { archived: 3 }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await archiveAgent('agent-1')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-definitions/agent-1',
      expect.objectContaining({ method: 'DELETE' })
    )
  })
})
