import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  fetchWorkflowDefinition,
  fetchWorkflows,
  publishWorkflowDraft,
  validateWorkflowDraft,
} from './workflows'

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

describe('workflows api', () => {
  it('lists workflows', async () => {
    const payload = { workflows: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await fetchWorkflows()

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith('/api/workflows', expect.anything())
  })

  it('fetches a workflow definition with an encoded key', async () => {
    const payload = { workflow: { key: 'video knowledge' } }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await fetchWorkflowDefinition('video knowledge')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workflows/video%20knowledge',
      expect.anything()
    )
  })

  it('validates a draft with a definition_yaml body', async () => {
    const payload = { valid: true, errors: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await validateWorkflowDraft('ws1', 'key: wf')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/workflow-drafts/validate',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ definition_yaml: 'key: wf' }),
      })
    )
  })

  it('publishes a draft with a definition_yaml body', async () => {
    const payload = { valid: true, errors: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await publishWorkflowDraft('ws 1', 'key: wf')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws%201/workflow-drafts/publish',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ definition_yaml: 'key: wf' }),
      })
    )
  })
})
