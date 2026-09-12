import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  DRAFT_NEVER_SAVED,
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

  it('carries the CAS base in the body when expectedUpdatedAt is set (#633)', async () => {
    const fetchMock = mockFetchJson({
      definition_yaml: 'key: wf',
      updated_at: '2026-09-12T00:00:00+00:00',
    })
    global.fetch = fetchMock

    await putWorkflowDraft('ws1', 'key: wf', {
      expectedUpdatedAt: '2026-09-11T00:00:00+00:00',
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/workflow-draft',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({
          definition_yaml: 'key: wf',
          expected_updated_at: '2026-09-11T00:00:00+00:00',
        }),
      })
    )
  })

  it('translates a 409 into WorkflowDraftConflictError with the current draft (#633)', async () => {
    const detail = {
      message: 'Workflow draft conflict: another session saved a newer draft.',
      expected_updated_at: '2026-09-11T00:00:00+00:00',
      current_draft: {
        definition_yaml: 'key: wf\nlabel: agent\n',
        updated_at: '2026-09-12T00:00:00+00:00',
      },
    }
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: () => Promise.resolve({ detail }),
      text: () => Promise.resolve(JSON.stringify({ detail })),
    } as unknown as Response)

    const error = await putWorkflowDraft('ws1', 'key: wf', {
      expectedUpdatedAt: DRAFT_NEVER_SAVED,
    }).then(
      () => null,
      (e: unknown) => e
    )

    expect(error).toBeInstanceOf(WorkflowDraftConflictError)
    expect((error as WorkflowDraftConflictError).currentDraft).toEqual({
      definition_yaml: 'key: wf\nlabel: agent\n',
      updated_at: '2026-09-12T00:00:00+00:00',
    })
  })
})
