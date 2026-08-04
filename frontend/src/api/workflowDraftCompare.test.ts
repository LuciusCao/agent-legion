import { afterEach, describe, expect, it, vi } from 'vitest'

import { compareWorkflowDraft } from './workflowDraftCompare'

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

describe('workflow draft compare api', () => {
  it('posts the compare request to the workspace endpoint', async () => {
    const payload = { changes: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock
    const request = { definition_yaml: 'key: wf\nnodes: {}' }

    const result = await compareWorkflowDraft('ws 1', request)

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws%201/workflow-drafts/compare',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(request) })
    )
  })
})
