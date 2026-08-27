import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchWorkflowDraft, putWorkflowDraft } from './workflowDraft'

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

describe('workflowDraft api', () => {
  it('fetches the stored draft', async () => {
    const payload = {
      definition_yaml: 'key: wf',
      updated_at: '2026-08-27T01:02:03+00:00',
    }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await fetchWorkflowDraft('ws1')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/workflow-draft',
      expect.objectContaining({ cache: 'no-store' })
    )
  })

  it('upserts the draft with a definition_yaml body', async () => {
    const payload = {
      definition_yaml: 'key: wf',
      updated_at: '2026-08-27T01:02:03+00:00',
    }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await putWorkflowDraft('ws 1', 'key: wf')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws%201/workflow-draft',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ definition_yaml: 'key: wf' }),
      })
    )
  })
})
