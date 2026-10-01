import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  createWorkspaceApiToken,
  listWorkspaceApiTokens,
  revokeWorkspaceApiToken,
} from './workspaceApiTokens'

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

describe('workspace api tokens api', () => {
  it('lists the workspace api tokens on the workspace-scoped path', async () => {
    const fetchMock = mockFetchJson({ tokens: [] })
    global.fetch = fetchMock

    const tokens = await listWorkspaceApiTokens('demo_video_workflow')

    expect(tokens).toEqual([])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/demo_video_workflow/api-tokens',
      expect.objectContaining({ cache: 'no-store' })
    )
  })

  it('creates an api token with label and optional ttl_hours', async () => {
    const fetchMock = mockFetchJson({
      token_id: 't1',
      api_token: 't1.secret',
      workspace_id: 'demo_video_workflow',
      label: 'cms-cron',
    })
    global.fetch = fetchMock

    const created = await createWorkspaceApiToken('demo_video_workflow', {
      label: 'cms-cron',
      ttl_hours: 48,
    })

    expect(created.api_token).toBe('t1.secret')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/demo_video_workflow/api-tokens',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ label: 'cms-cron', ttl_hours: 48 }),
      })
    )
  })

  it('revokes an api token with both ids encoded', async () => {
    const fetchMock = mockFetchJson({ token_id: 't1', revoked: true })
    global.fetch = fetchMock

    await revokeWorkspaceApiToken('demo_video_workflow', 'token/1')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/demo_video_workflow/api-tokens/token%2F1',
      expect.objectContaining({ method: 'DELETE' })
    )
  })
})
