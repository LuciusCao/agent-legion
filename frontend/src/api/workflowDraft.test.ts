import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  WorkflowDraftConflictError,
  fetchWorkflowDraft,
  putWorkflowDraft,
} from './workflowDraft'

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

  it('carries the CAS base generation in the body when set (#633/#1226)', async () => {
    const fetchMock = mockFetchJson({
      definition_yaml: 'key: wf',
      updated_at: '2026-09-12T00:00:00+00:00',
    })
    global.fetch = fetchMock

    await putWorkflowDraft('ws1', 'key: wf', {
      baseGeneration: 4,
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/workflow-draft',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({
          definition_yaml: 'key: wf',
          base_generation: 4,
        }),
      })
    )
  })

  it('translates a 409 into WorkflowDraftConflictError with the current draft (#633)', async () => {
    const detail = {
      message: 'Workflow draft conflict: another session saved a newer draft.',
      base_generation: 4,
      current_draft: {
        definition_yaml: 'key: wf\nlabel: agent\n',
        updated_at: '2026-09-12T00:00:00+00:00',
        generation: 5,
      },
    }
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ detail }),
      text: () => Promise.resolve(JSON.stringify({ detail })),
    } as unknown as Response)

    const error = await putWorkflowDraft('ws1', 'key: wf', {
      baseGeneration: 0,
    }).then(
      () => null,
      (e: unknown) => e
    )

    expect(error).toBeInstanceOf(WorkflowDraftConflictError)
    expect((error as WorkflowDraftConflictError).currentDraft).toEqual({
      definition_yaml: 'key: wf\nlabel: agent\n',
      updated_at: '2026-09-12T00:00:00+00:00',
      // #1143：current_draft 现携带语义身份（此 mock detail 未带 → null）。
      definition_hash: null,
      // #1226：代次随冲突响应传播（采用/重 base 用）。
      generation: 5,
    })
  })
})
